from __future__ import annotations
import json
import re
import requests
from .config import OLLAMA_URL, load_settings
from .personality import prompt_for, random_micro_shift

GAME_PROFILES = {'generic':'Entorno interactivo generico.','roblox':'Roblox: aprende controles y usa WASD, mouse, espacio y clic.','limbus':'Limbus Company: lee la interfaz y usa acciones de combate prudentes.'}
ACTION_HINT = 'Devuelve SOLO JSON con reply y actions. Las coordenadas son 0..1000. Acciones: click, double_click, move, drag, press, hold, keys, key_down, key_up, type, scroll, wait, open_url, open_app, remember, social_update, self_update, drive_update, private_note, done.'

class Agent:
    def __init__(self, memory, computer, web, on_status=None, on_token=None):
        self.memory = memory
        self.computer = computer
        self.web = web
        self.on_status = on_status or (lambda s: None)
        self.on_token = on_token or (lambda s: None)
        self.settings = load_settings()
        self.history = []
        self.stop_event = __import__('threading').Event()

    def set_settings(self, settings):
        self.settings = settings

    def stop(self):
        self.stop_event.set()
        try: self.computer.stop()
        except Exception: pass

    def reset_stop(self):
        self.stop_event.clear()
        try: self.computer.clear_stop()
        except Exception: pass

    def _context(self):
        return prompt_for(self.memory.self_state(), self.memory.drives(), self.memory.people(), self.memory.recall('', 8), self.memory.recent_episodes(5))

    def _call(self, messages, model, timeout=45, num_predict=220, ctx=3072):
        payload = {'model':model,'messages':messages,'stream':False,'think':False,'format':'json','keep_alive':'30m','options':{'temperature':0.25,'num_ctx':ctx,'num_predict':num_predict}}
        r = requests.post(OLLAMA_URL + '/api/chat', json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get('message',{}).get('content','')

    @staticmethod
    def _parse(raw):
        text = re.sub(r'<think>.*?</think>', '', str(raw), flags=re.I|re.S).strip()
        text = re.sub(r'^```json\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
        try: value = json.loads(text)
        except Exception:
            a,b=text.find('{'),text.rfind('}')
            value=json.loads(text[a:b+1]) if a>=0 and b>a else {'reply':text,'actions':[]}
        return value if isinstance(value,dict) else {'reply':'','actions':[]}

    @staticmethod
    def _normalize(result):
        if not isinstance(result,dict): return {'reply':'','actions':[]}
        actions=result.get('actions',[])
        if isinstance(actions,dict): actions=[actions]
        if not isinstance(actions,list): actions=[]
        if not actions and isinstance(result.get('action'),dict): actions=[result['action']]
        if not actions and result.get('key'): actions=[{'type':'press','key':str(result['key'])}]
        if not actions and 'x' in result and 'y' in result: actions=[{'type':'click','x':result['x'],'y':result['y'],'normalized':True,'button':str(result.get('button','left'))}]
        return {'reply':str(result.get('reply',result.get('message',''))),'actions':actions}

    def _apply_memory_actions(self,result):
        clean=[]
        for action in result.get('actions',[]):
            kind=str(action.get('type','')).lower()
            if kind=='remember': self.memory.remember(str(action.get('text','')),str(action.get('kind','general')),0.6)
            elif kind=='social_update': self.memory.adjust_person(str(action.get('person','')),action.get('deltas',{}),str(action.get('note','')))
            elif kind=='self_update': self.memory.adjust_self(action.get('changes',{}))
            elif kind=='drive_update': self.memory.adjust_drive(str(action.get('drive','')),float(action.get('delta',0)),str(action.get('reason','')))
            elif kind=='private_note': self.memory.add_private_note(str(action.get('text','')))
            else: clean.append(action)
        return {'reply':str(result.get('reply','')),'actions':clean}

    def chat(self,text,automation_allowed=False):
        self.reset_stop()
        model=self.settings.get('text_model','qwen3:1.7b')
        system=self._context()+'\n\n'+ACTION_HINT
        if not automation_allowed: system+='\nAutonomia de PC desactivada.'
        messages=[{'role':'system','content':system}] + self.history[-8:] + [{'role':'user','content':text}]
        try: result=self._normalize(self._parse(self._call(messages,model)))
        except Exception as exc: return {'reply':'Error local: '+str(exc),'actions':[]}
        self.history += [{'role':'user','content':text},{'role':'assistant','content':result.get('reply','')}]
        return self._apply_memory_actions(result)

    def vision(self,goal,images_b64,profile='generic'):
        self.reset_stop()
        model=self.settings.get('vision_model','qwen3-vl:2b')
        system=self._context()+'\n\n'+ACTION_HINT+'\nPERFIL: '+GAME_PROFILES.get(profile,GAME_PROFILES['generic'])+'\nOBJETIVO: '+str(goal)
        msg={'role':'user','content':'Es el fotograma mas reciente del juego. Actua sobre el estado actual ahora.','images':images_b64}
        try: return self._normalize(self._parse(self._call([{'role':'system','content':system},msg],model,18,100,2048)))
        except Exception as exc: return {'reply':'','actions':[],'error':str(exc)}

    def execute_actions(self,result,autonomy_allowed=False):
        if not autonomy_allowed: return 'autonomia apagada'
        logs=[]
        for action in result.get('actions',[])[:int(self.settings.get('game_max_actions',6))]:
            if self.stop_event.is_set(): break
            kind=str(action.get('type','')).lower()
            if kind=='search_web':
                try: logs.append('web:'+ ' | '.join(x.get('title','') for x in self.web(str(action.get('query','')))[:3]))
                except Exception as exc: logs.append('web error:'+str(exc))
                continue
            if kind in {'remember','social_update','self_update','drive_update','private_note','done'}: continue
            logs.append(kind+':'+str(self.computer.act(action)))
        try: self.memory.adjust_self(random_micro_shift())
        except Exception: pass
        return ' | '.join(logs)
