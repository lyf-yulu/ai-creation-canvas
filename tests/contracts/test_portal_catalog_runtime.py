from __future__ import annotations

import asyncio, hashlib, hmac, json, time
from urllib.parse import quote

import httpx, pytest
from fastapi.testclient import TestClient

from ai_creation_canvas.adapters.portal.catalog import ModelCatalog
from ai_creation_canvas.app import create_app
from ai_creation_canvas.config import Settings
from ai_creation_canvas.domain.models import ModelSpec, PortalUser, RequestContext
from ai_creation_canvas.domain.registry import AdapterRegistry

def headers():
    ts=str(int(time.time())); p=f"v2\n{ts}\nu\nuser\n{quote('A',safe='')}"; sig=hmac.new(b'test-secret',p.encode(),hashlib.sha256).hexdigest()
    return {'X-Portal-Sig-Version':'2','X-Portal-Timestamp':ts,'X-Portal-User-Id':'u','X-Portal-Username':'A','X-Portal-Role':'user','X-Portal-Signature':sig}

def test_app_config_wires_models_and_current_cookie(tmp_path):
    cfg=tmp_path/'services.json'; cfg.write_text(json.dumps({'services':[{'service_id':'image','mount':'/image','service_type':'image','operations':['image.generate']},{'service_id':'video','mount':'/video','service_type':'video','operations':['video.generate']}]}))
    seen=[]
    def handler(r):
        seen.append(r.headers.get('cookie')); kind='image' if r.url.path.startswith('/image') else 'video'
        return httpx.Response(200,headers={'content-type':'application/json'},json={'models':[{'id':kind+'1','display_name':kind,'operations':[kind+'.generate']},{'id':kind+'2','display_name':kind+'2','operations':[kind+'.generate']}]})
    settings=Settings('test',8992,tmp_path/'data','test-secret',portal_base_url='https://portal.test',services_config_path=cfg,services_config_root=tmp_path)
    client=TestClient(create_app(settings,portal_transport=httpx.MockTransport(handler)),raise_server_exceptions=False)
    assert len(client.get('/api/v1/models',headers={**headers(),'Cookie':'s=a'}).json()['models'])==4
    client.get('/api/v1/models',headers={**headers(),'Cookie':'s=b'})
    assert seen==['s=a','s=a','s=b','s=b']

@pytest.mark.anyio
async def test_catalog_runs_adapters_concurrently_and_propagates_cancel():
    started=asyncio.Event(); release=asyncio.Event(); count=0
    class A:
        def __init__(self,id): self.service_id=id
        async def list_models(self,c):
            nonlocal count; count+=1
            if count==2: started.set()
            await release.wait(); return (ModelSpec(self.service_id,self.service_id,self.service_id,('image.generate',)),)
        async def submit(self,c,r): pass
        async def poll(self,c,i): pass
    r=AdapterRegistry();r.register_generation(A('b'));r.register_generation(A('a')); task=asyncio.create_task(ModelCatalog(r).list_models(RequestContext(PortalUser('u','A','user'),'r','t')))
    await asyncio.wait_for(started.wait(),1); release.set(); assert [x.model_id for x in (await task).models]==['a','b']

@pytest.mark.anyio
async def test_catalog_cancellation_propagates_and_cancels_adapter():
    started=asyncio.Event(); cancelled=asyncio.Event()
    class A:
        service_id='a'
        async def list_models(self,c):
            started.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        async def submit(self,c,r): pass
        async def poll(self,c,i): pass
    r=AdapterRegistry();r.register_generation(A()); task=asyncio.create_task(ModelCatalog(r).list_models(RequestContext(PortalUser('u','A','user'),'r','t')))
    await started.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert cancelled.is_set()
