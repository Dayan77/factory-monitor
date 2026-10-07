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
def recompute(body,t,timeout_s=1800,poll_s=2.0):
    """POST /api/predictive/recompute as a background job (202 + job id), then poll until it ends.
    Returns (status, body) like `call`: (200, result) when done; the job's error otherwise."""
    import time
    s,job=call("POST","/api/predictive/recompute",{**body,"background":True},t)
    if s!=202: return s,job
    end=time.time()+timeout_s
    while time.time()<end:
        s,j=call("GET",f"/api/predictive/recompute/jobs/{job['job_id']}",t=t)
        if s!=200: return s,j
        if j["status"]=="done": return 200,j["result"]
        if j["status"]=="failed": return 500,j
        time.sleep(poll_s)
    return 504,{"job_id":job["job_id"],"error":"timeout"}
