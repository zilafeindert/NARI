from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import numpy as np

class Memory:
    def __init__(self, path: Path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self._init()

    def _columns(self, table):
        return {str(r["name"]) for r in self.db.execute(f"PRAGMA table_info({table})")}

    def _ensure_schema(self):
        cols=self._columns("drives")
        if not cols:
            self.db.execute("CREATE TABLE drives(name TEXT PRIMARY KEY,score REAL DEFAULT 0,reason TEXT DEFAULT '')")
            cols=self._columns("drives")
        if "score" not in cols:
            self.db.execute("ALTER TABLE drives ADD COLUMN score REAL DEFAULT 0")
            if "value" in cols:
                self.db.execute("UPDATE drives SET score=COALESCE(value,0)")
            elif "strength" in cols:
                self.db.execute("UPDATE drives SET score=COALESCE(strength,0)")
        if "reason" not in cols:
            self.db.execute("ALTER TABLE drives ADD COLUMN reason TEXT DEFAULT ''")
        self.db.commit()

    def _init(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY,text TEXT,kind TEXT,importance REAL,created REAL DEFAULT (strftime('%s','now')));
        CREATE TABLE IF NOT EXISTS people(name TEXT PRIMARY KEY,embedding TEXT,affection REAL DEFAULT 0,trust REAL DEFAULT 0,respect REAL DEFAULT 0,curiosity REAL DEFAULT 0,comfort REAL DEFAULT 0,annoyance REAL DEFAULT 0,fear REAL DEFAULT 0,admiration REAL DEFAULT 0,note TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS episodes(id INTEGER PRIMARY KEY,kind TEXT,title TEXT,summary TEXT,created REAL DEFAULT (strftime('%s','now')));
        CREATE TABLE IF NOT EXISTS self_state(key TEXT PRIMARY KEY,value REAL);
        CREATE TABLE IF NOT EXISTS drives(name TEXT PRIMARY KEY,score REAL DEFAULT 0,reason TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS private_notes(id INTEGER PRIMARY KEY,text TEXT,created REAL DEFAULT (strftime('%s','now')));
        """)
        self._ensure_schema()
        defaults={"mood":0.15,"energy":0.8,"curiosity":0.55,"confidence":0.4,"social":0.35,"stress":0.0,"playfulness":0.6,"attachment":0.2}
        for k,v in defaults.items():
            self.db.execute("INSERT OR IGNORE INTO self_state(key,value) VALUES(?,?)",(k,v))
        for n in ("aprender","explorar","conectar","jugar"):
            self.db.execute("INSERT OR IGNORE INTO drives(name,score,reason) VALUES(?,?,?)",(n,0.2,"initial"))
        self.db.commit()

    def self_state(self):
        return {r["key"]:float(r["value"]) for r in self.db.execute("SELECT key,value FROM self_state")}

    def adjust_self(self, changes):
        for k,v in (changes or {}).items():
            try:
                delta=float(v)
                self.db.execute(
                    "INSERT INTO self_state(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=max(-1,min(1,value+excluded.value))",
                    (str(k),delta)
                )
            except Exception:
                pass
        self.db.commit()

    def drives(self):
        rows=self.db.execute("SELECT name,score,reason FROM drives ORDER BY score DESC").fetchall()
        return [(r["name"],float(r["score"] or 0),str(r["reason"] or "")) for r in rows]

    def adjust_drive(self,name,delta,reason=""):
        name=str(name or "explorar")
        try: delta=float(delta)
        except Exception: delta=0.0
        row=self.db.execute("SELECT name,score FROM drives WHERE name=?",(name,)).fetchone()
        if row:
            new=max(0,min(1,float(row["score"] or 0)+delta))
            self.db.execute("UPDATE drives SET score=?,reason=? WHERE name=?",(new,str(reason or row["name"]),name))
        else:
            self.db.execute("INSERT INTO drives(name,score,reason) VALUES(?,?,?)",(name,max(0,min(1,delta)),str(reason or "created")))
        self.db.commit()

    def remember(self,text,kind="general",importance=0.5):
        text=str(text or "").strip()
        if text:
            self.db.execute("INSERT INTO memories(text,kind,importance) VALUES(?,?,?)",(text,str(kind),float(importance)))
            self.db.commit()

    def recall(self,query="",limit=8):
        rows=list(self.db.execute("SELECT * FROM memories ORDER BY importance DESC,created DESC LIMIT ?",(int(limit),)))
        return [dict(r) for r in rows]

    def add_episode(self,kind,title,summary,extra=""):
        summary=str(summary)
        if extra: summary += " | "+str(extra)
        self.db.execute("INSERT INTO episodes(kind,title,summary) VALUES(?,?,?)",(str(kind),str(title),summary))
        self.db.commit()

    def recent_episodes(self,limit=5):
        return [dict(r) for r in self.db.execute("SELECT * FROM episodes ORDER BY created DESC LIMIT ?",(int(limit),))]

    def private_notes(self,limit=20):
        return [r["text"] for r in self.db.execute("SELECT text FROM private_notes ORDER BY created DESC LIMIT ?",(int(limit),))]

    def add_private_note(self,text):
        text=str(text or "").strip()
        if text:
            self.db.execute("INSERT INTO private_notes(text) VALUES(?)",(text,))
            self.db.commit()

    def people(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM people ORDER BY name")]

    def person(self,name):
        r=self.db.execute("SELECT * FROM people WHERE name=?",(str(name),)).fetchone()
        return dict(r) if r else None

    def upsert_person(self,name,embedding):
        emb=np.asarray(embedding,dtype=np.float32).flatten().tolist() if embedding is not None else []
        self.db.execute(
            "INSERT INTO people(name,embedding) VALUES(?,?) "
            "ON CONFLICT(name) DO UPDATE SET embedding=excluded.embedding",
            (str(name),json.dumps(emb))
        )
        self.db.commit()

    def person_embeddings(self):
        out={}
        for r in self.db.execute("SELECT name,embedding FROM people"):
            try: out[r["name"]]=np.asarray(json.loads(r["embedding"]),dtype=np.float32)
            except Exception: pass
        return out

    def adjust_person(self,name,deltas,note=""):
        name=str(name or "").strip()
        if not name: return
        if not self.person(name):
            self.db.execute("INSERT INTO people(name,note) VALUES(?,?)",(name,str(note or "")))
            self.db.commit()
        fields=["affection","trust","respect","curiosity","comfort","annoyance","fear","admiration"]
        sets=[]; vals=[]
        for k,v in (deltas or {}).items():
            if k in fields:
                try: val=float(v)
                except Exception: continue
                sets.append(f"{k}=max(-1,min(1,{k}+?))"); vals.append(val)
        if note:
            sets.append("note=?"); vals.append(str(note))
        if sets:
            self.db.execute("UPDATE people SET "+",".join(sets)+" WHERE name=?",(*vals,name))
            self.db.commit()
