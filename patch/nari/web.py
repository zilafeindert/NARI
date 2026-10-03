from __future__ import annotations
import requests
from bs4 import BeautifulSoup

def search(query):
    query=str(query or "").strip()
    if not query:return []
    r=requests.get("https://html.duckduckgo.com/html/",params={"q":query},headers={"User-Agent":"Mozilla/5.0 NARI/5.2.3"},timeout=12)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser")
    rows=[]
    for a in soup.select(".result__a")[:6]:
        title=a.get_text(" ",strip=True); href=a.get("href","")
        rows.append({"title":title,"url":href})
    return rows
