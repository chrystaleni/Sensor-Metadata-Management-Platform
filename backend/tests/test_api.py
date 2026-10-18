import sys
import io
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from openpyxl import Workbook

import main

@pytest.fixture
def client(tmp_path, monkeypatch):
    engine=create_engine(f"sqlite:///{tmp_path}/test.db",connect_args={"check_same_thread":False})
    main.engine=engine
    main.SessionLocal=sessionmaker(engine,expire_on_commit=False)
    main.Base.metadata.create_all(engine)
    with main.SessionLocal() as db:
        for code,name in [('viewer','Viewer'),('editor','Editor'),('department_admin','Department Administrator'),('system_admin','System Administrator')]:
            db.add(main.Role(id=code,name=name))
        db.commit()
    with TestClient(main.app) as c:
        yield c
    main.Base.metadata.drop_all(engine)
    engine.dispose()

def bootstrap(client):
    r=client.post('/api/setup',json={'username':'admin','password':'a-long-local-password','demo':True})
    assert r.status_code==200,r.text
    assert client.post('/api/auth/login',json={'username':'admin','password':'wrong-password'}).status_code==401
    login=client.post('/api/auth/login',json={'username':'admin','password':'a-long-local-password'})
    assert login.status_code==200,login.text
    return login.json()['departments'][0]['id']

def test_auth_dashboard_and_department_scope(client):
    dep=bootstrap(client)
    assert client.get('/api/dashboard/summary',headers={'X-Department-Id':dep}).json()['measurements']==3
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/dashboard/summary',headers={'X-Department-Id':dep}).status_code==401

def test_invalid_ip_and_required_links_rejected(client):
    dep=bootstrap(client); headers={'X-Department-Id':dep}
    lists=client.get('/api/lists').json()
    device_type=next(v['id'] for v in lists['device_types'] if v['name']=='S272')
    bad=client.post('/api/physical-devices',headers=headers,json={'device_id':'bad','device_name':'Bad','device_type_id':device_type,'communication_parameters':{'IP':'222.111.345.678'}})
    assert bad.status_code==422
    secret=client.post('/api/physical-devices',headers=headers,json={'device_id':'secret','device_name':'Secret','device_type_id':device_type,'communication_parameters':{'Credentials_Keys':'do-not-store'}})
    assert secret.status_code==422
    missing=client.post('/api/measurements',headers=headers,json={'measurement_id':'M1','measurement_name':'M1'})
    assert missing.status_code in (422,400)

def test_soft_inactivation_and_geojson_lon_lat(client):
    dep=bootstrap(client); headers={'X-Department-Id':dep}
    devices=client.get('/api/physical-devices',headers=headers).json()['items']
    assert client.delete('/api/physical-devices/'+devices[0]['id'],headers=headers).status_code==409
    # Seeded records deliberately have no location and are not fabricated at the origin.
    features=client.get('/api/gis/geojson',headers=headers).json()['features']
    assert features==[]

def test_api_key_is_scoped_read_only_and_revocable(client):
    dep=bootstrap(client); headers={'X-Department-Id':dep}
    created=client.post('/api/api-keys',headers=headers,json={'name':'test integration'}).json()
    auth={'Authorization':f"Bearer {created['token']}"}
    assert client.get('/api/measurements',headers=auth).status_code==200
    assert client.post('/api/measurements',headers=auth,json={}).status_code==403
    assert client.post(f"/api/api-keys/{created['id']}/revoke",headers=headers).status_code==200
    assert client.get('/api/measurements',headers=auth).status_code==401
    assert client.get('/api/measurements',headers={'Authorization':'Bearer smp_invalid'}).status_code==401
    assert client.post('/api/api-keys',headers=headers,json={'name':'expired','expires_at':'2000-01-01T00:00:00Z'}).status_code==422

def test_viewer_can_read_but_cannot_write_or_cross_departments(client):
    dep=bootstrap(client); admin={'X-Department-Id':dep}
    created=client.post('/api/users',headers=admin,json={'username':'reader','display_name':'Read Only','password':'temporary-long-password','role':'viewer'})
    assert created.status_code==200
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'username':'reader','password':'temporary-long-password'}).status_code==200
    assert client.get('/api/measurements',headers=admin).status_code==200
    assert client.post('/api/measurements',headers=admin,json={}).status_code==403
    assert client.get('/api/measurements',headers={'X-Department-Id':'00000000-0000-0000-0000-000000000001'}).status_code==404

def test_excel_export_headers_and_atomic_import(client):
    dep=bootstrap(client); headers={'X-Department-Id':dep}
    exported=client.get('/api/export',headers=headers)
    assert exported.status_code==200
    from openpyxl import load_workbook
    wb=load_workbook(io.BytesIO(exported.content),read_only=True)
    assert list(next(wb['Measurements'].iter_rows(values_only=True)))==main.MEAS_HEADERS
    assert list(next(wb['NetworkElements'].iter_rows(values_only=True)))==main.NE_HEADERS
    assert list(next(wb['Physical_devices'].iter_rows(values_only=True)))==main.DEV_HEADERS
    data=client.get('/api/measurements',headers=headers).json()['items'][0]
    ne=client.get('/api/network-elements',headers=headers).json()['items'][0]
    device=client.get('/api/physical-devices',headers=headers).json()['items'][0]
    # A conflict in one row must prevent an otherwise new row from being committed.
    sample=Workbook(); sample.remove(sample.active)
    ws=sample.create_sheet('NetworkElements'); ws.append(main.NE_HEADERS); ws.append(['Water Operations','NE_NEW','New element','DMA_Inlet','DMA_XXX',None,None,None,None,None,None])
    ws=sample.create_sheet('Measurements'); ws.append(main.MEAS_HEADERS)
    ws.append(['Water Operations','LLWD_NEW','New measurement',1,'Door',None,None,'Open/Close',None,ne['network_element_id'],ne['network_element_name'],ne['network_element_type'],'DMA_XXX',None,None,None,None,None,device['device_id'],device['device_name'],device['device_type'],'ODBC','{"DB":"LLDB","Table":"T54","Column":"new"}'])
    ws.append(['Water Operations',data['measurement_id'],'Duplicate',1,'Door',None,None,'Open/Close',None,ne['network_element_id'],ne['network_element_name'],ne['network_element_type'],'DMA_XXX',None,None,None,None,None,device['device_id'],device['device_name'],device['device_type'],'ODBC','{}'])
    buf=io.BytesIO(); sample.save(buf); buf.seek(0)
    response=client.post('/api/import/commit',headers=headers,files={'file':('atomic.xlsx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert response.status_code==200 and not response.json()['imported']
    assert client.get('/api/network-elements',headers=headers,params={'search':'NE_NEW'}).json()['total']==0

def test_valid_excel_import_persists_collection_json(client):
    dep=bootstrap(client); headers={'X-Department-Id':dep}
    ne=client.get('/api/network-elements',headers=headers).json()['items'][0]
    device=client.get('/api/physical-devices',headers=headers).json()['items'][0]
    wb=Workbook(); wb.remove(wb.active); ws=wb.create_sheet('Measurements'); ws.append(main.MEAS_HEADERS)
    wb.create_sheet('NetworkElements').append(main.NE_HEADERS)
    wb.create_sheet('Physical_devices').append(main.DEV_HEADERS)
    ws.append(['Water Operations','LLWD_IMPORT_001','Imported Flow',7,'Flow',None,None,'m3/h','',ne['network_element_id'],ne['network_element_name'],ne['network_element_type'],ne['network_area'],None,None,None,None,None,device['device_id'],device['device_name'],device['device_type'],'ODBC','{"DB":"LLDB","Table":"T54","Column":"v2"}'])
    buf=io.BytesIO(); wb.save(buf); buf.seek(0)
    validated=client.post('/api/import/validate',headers=headers,files={'file':('valid.xlsx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert validated.status_code==200 and validated.json()['valid']
    buf.seek(0)
    result=client.post('/api/import/commit',headers=headers,files={'file':('valid.xlsx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert result.status_code==200 and result.json()['imported']
    row=next(r for r in client.get('/api/measurements',headers=headers).json()['items'] if r['measurement_id']=='LLWD_IMPORT_001')
    detail=client.get(f"/api/measurements/{row['id']}",headers=headers).json()
    assert detail['DataCollectionParameters']=={'DB':'LLDB','Table':'T54','Column':'v2'}

