import json,urllib.request,os
API=os.environ.get("API","http://127.0.0.1:8160")
def call(m,p,b=None,t=None,raw=False):
    r=urllib.request.Request(API+p,method=m,data=json.dumps(b).encode() if b is not None else None,headers={"Content-Type":"application/json",**({"Authorization":"Bearer "+t} if t else {})})
    try:
        with urllib.request.urlopen(r,timeout=120) as x:
            d=x.read(); return x.status,(d if raw else json.loads(d or b"null"))
    except urllib.error.HTTPError as e:
        d=e.read()
        try: return e.code,json.loads(d or b"null")
        except Exception: return e.code,d
def login(e="admin@factory-monitor.com",p="Admin@1234"):
    return call("POST","/api/auth/login",{"email":e,"password":p})[1]["access_token"]
