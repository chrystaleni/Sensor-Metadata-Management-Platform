"""Sensor Metadata Management API. Live sensor readings are intentionally out of scope."""
from __future__ import annotations
import csv, hashlib, io, json, os, secrets, uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import (create_engine, String, Boolean, DateTime, Integer, Float, ForeignKey,
    UniqueConstraint, ForeignKeyConstraint, CheckConstraint, Index, JSON, Text, select, func, and_)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session, sessionmaker
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware
from pwdlib import PasswordHash
from geoalchemy2 import Geometry
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import shape, mapping, Point

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sensor_metadata.db")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
password_hash = PasswordHash.recommended()
GIS_TYPE = Geometry('POINT',srid=4326,spatial_index=True) if DATABASE_URL.startswith('postgresql') else JSON()
NETWORK_GIS_TYPE = Geometry('GEOMETRY',srid=4326,spatial_index=True) if DATABASE_URL.startswith('postgresql') else JSON()

class Base(DeclarativeBase): pass
class Audit:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class Organisation(Audit, Base):
    __tablename__='organisations'; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); name: Mapped[str]=mapped_column(String(160), unique=True); departments: Mapped[list['Department']]=relationship(back_populates='organisation')
class Department(Audit, Base):
    __tablename__='departments'; __table_args__=(UniqueConstraint('organisation_id','name'),)
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); organisation_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('organisations.id')); name: Mapped[str]=mapped_column(String(160)); organisation: Mapped[Organisation]=relationship(back_populates='departments')
class User(Audit, Base):
    __tablename__='users'; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); username: Mapped[str]=mapped_column(String(120), unique=True); display_name: Mapped[str]=mapped_column(String(160)); password_hash: Mapped[str]=mapped_column(String(255)); is_system_admin: Mapped[bool]=mapped_column(Boolean, default=False)
class Role(Base):
    __tablename__='roles'; id: Mapped[str]=mapped_column(String(40), primary_key=True); name: Mapped[str]=mapped_column(String(80), unique=True)
class Membership(Base):
    __tablename__='user_departments'; __table_args__=(UniqueConstraint('user_id','department_id'),)
    id: Mapped[int]=mapped_column(Integer, primary_key=True); user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id')); department_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('departments.id')); role_id: Mapped[str]=mapped_column(ForeignKey('roles.id'))
class ListValue(Audit, Base):
    __tablename__='controlled_lists'; __table_args__=(UniqueConstraint('list_name','name'),)
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); list_name: Mapped[str]=mapped_column(String(40), index=True); name: Mapped[str]=mapped_column(String(120)); code: Mapped[str|None]=mapped_column(String(120), nullable=True); description: Mapped[str|None]=mapped_column(Text, nullable=True); standard_uri: Mapped[str|None]=mapped_column(String(500), nullable=True)
class LOVPair(Audit,Base):
    __tablename__='operational_vocabulary'; __table_args__=(UniqueConstraint('measurement_type_id','value_code'),)
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); measurement_type_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('controlled_lists.id')); value_code: Mapped[str]=mapped_column(String(120)); description: Mapped[str|None]=mapped_column(Text,nullable=True)
class PhysicalDevice(Audit, Base):
    __tablename__='physical_devices'; __table_args__=(UniqueConstraint('department_id','device_id'), UniqueConstraint('id','department_id'), CheckConstraint("length(trim(device_id)) > 0"), CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90"), CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180"))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); department_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('departments.id'), index=True); device_id: Mapped[str]=mapped_column(String(160)); device_name: Mapped[str]=mapped_column(String(200)); device_type_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('controlled_lists.id')); parent_device_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey('physical_devices.id'), nullable=True); communication_parameters: Mapped[dict|None]=mapped_column(JSON().with_variant(JSONB, 'postgresql'), nullable=True); longitude: Mapped[float|None]=mapped_column(Float, nullable=True); latitude: Mapped[float|None]=mapped_column(Float, nullable=True); location: Mapped[Any|None]=mapped_column(GIS_TYPE, nullable=True); manufacturer: Mapped[str|None]=mapped_column(String(160), nullable=True); model: Mapped[str|None]=mapped_column(String(160), nullable=True); serial_number: Mapped[str|None]=mapped_column(String(160), nullable=True); installation_date: Mapped[str|None]=mapped_column(String(40), nullable=True)
class NetworkElement(Audit, Base):
    __tablename__='network_elements'; __table_args__=(UniqueConstraint('department_id','network_element_id'), UniqueConstraint('id','department_id'), CheckConstraint("length(trim(network_element_id)) > 0"), CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90"), CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180"), CheckConstraint("GeometryType(geometry) IN ('POINT','LINESTRING','POLYGON')").ddl_if(dialect='postgresql'))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); department_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('departments.id'), index=True); network_element_id: Mapped[str]=mapped_column(String(160)); network_element_name: Mapped[str]=mapped_column(String(200)); network_element_type_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('controlled_lists.id')); network_area: Mapped[str|None]=mapped_column(String(160), nullable=True); latitude: Mapped[float|None]=mapped_column(Float, nullable=True); longitude: Mapped[float|None]=mapped_column(Float, nullable=True); elevation: Mapped[float|None]=mapped_column(Float, nullable=True); strategic_id: Mapped[str|None]=mapped_column(String(160), nullable=True); dma_id: Mapped[str|None]=mapped_column(String(160), nullable=True); address: Mapped[str|None]=mapped_column(Text, nullable=True); network_element_code: Mapped[str|None]=mapped_column(String(160), nullable=True); district: Mapped[str|None]=mapped_column(String(160), nullable=True); geometry: Mapped[Any|None]=mapped_column(NETWORK_GIS_TYPE, nullable=True)
class Measurement(Audit, Base):
    __tablename__='measurements'; __table_args__=(UniqueConstraint('department_id','measurement_id'), UniqueConstraint('id','department_id'), CheckConstraint("length(trim(measurement_id)) > 0"), ForeignKeyConstraint(['physical_device_id','department_id'],['physical_devices.id','physical_devices.department_id'],name='fk_measurement_device_department'), ForeignKeyConstraint(['network_element_id','department_id'],['network_elements.id','network_elements.department_id'],name='fk_measurement_element_department'))
    id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); department_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('departments.id'), index=True); measurement_id: Mapped[str]=mapped_column(String(160)); measurement_name: Mapped[str]=mapped_column(String(200)); measurement_collection_batch: Mapped[int|None]=mapped_column(Integer, nullable=True); measurement_type_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('controlled_lists.id')); measurement_unit_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('controlled_lists.id')); meter_account: Mapped[str|None]=mapped_column(String(160), nullable=True); customer_account: Mapped[str|None]=mapped_column(String(160), nullable=True); details: Mapped[str|None]=mapped_column(Text, nullable=True); network_element_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('network_elements.id')); physical_device_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('physical_devices.id'))
class DataCollectionConfiguration(Audit, Base):
    __tablename__='data_collection_configurations'; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); measurement_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('measurements.id'),unique=True,index=True); collection_type: Mapped[str|None]=mapped_column(String(80),nullable=True); parameters: Mapped[dict|None]=mapped_column(JSON().with_variant(JSONB,'postgresql'),nullable=True)
class APIKey(Base):
    __tablename__='api_keys'; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4); department_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('departments.id')); name: Mapped[str]=mapped_column(String(120)); key_hash: Mapped[str]=mapped_column(String(64), unique=True); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)); expires_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True); revoked_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True), nullable=True)
class ActivityLog(Base):
    __tablename__='activity_logs'; id: Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); actor: Mapped[str]=mapped_column(String(120)); department_id: Mapped[uuid.UUID|None]=mapped_column(ForeignKey('departments.id'),nullable=True,index=True); action: Mapped[str]=mapped_column(String(80)); entity: Mapped[str]=mapped_column(String(80)); entity_id: Mapped[str|None]=mapped_column(String(160),nullable=True); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc)); safe_metadata: Mapped[dict|None]=mapped_column(JSON().with_variant(JSONB,'postgresql'),nullable=True)

class Input(BaseModel):
    model_config=ConfigDict(extra='allow')
    def get(self,key,default=None): return getattr(self,key,default)
    def __getitem__(self,key): return getattr(self,key)
class Login(Input): username:str; password:str
class EntityInput(Input):
    department_id: uuid.UUID

app=FastAPI(title='Sensor Metadata Management API', version='1.0.0', description='Metadata management API. No time-series readings are stored.')
app.add_middleware(SessionMiddleware, secret_key=os.getenv('SESSION_SECRET','local-development-only-change-me'), https_only=os.getenv('HTTPS_ONLY','false').lower()=='true', same_site='lax', max_age=8*60*60)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv('CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','), allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

def db():
    with SessionLocal() as s: yield s
def current_user(request:Request, s:Session=Depends(db)):
    bearer=request.headers.get('Authorization','')
    if bearer.startswith('Bearer '):
        token=bearer[7:]; candidate=hashlib.sha256(token.encode()).hexdigest()
        key=s.scalar(select(APIKey).where(APIKey.key_hash==candidate,APIKey.revoked_at.is_(None)))
        now=datetime.now(timezone.utc)
        if key and (key.expires_at is None or key.expires_at.replace(tzinfo=timezone.utc)>now):
            request.state.api_key_department_id=key.department_id
            return User(id=uuid.uuid4(),username=f'api-key:{key.name}',display_name=key.name,password_hash='',is_system_admin=False)
        raise HTTPException(401,'Invalid, expired or revoked API key')
    uid=request.session.get('uid')
    user=s.get(User, uuid.UUID(uid)) if uid else None
    if not user or not user.is_active: raise HTTPException(401,'Authentication required')
    now=datetime.now(timezone.utc)
    last=request.session.get('last_activity')
    if last:
        try: previous=datetime.fromisoformat(last)
        except ValueError: previous=now
        if (now-previous).total_seconds()>8*60*60:
            request.session.clear(); raise HTTPException(401,'Session expired after inactivity')
    request.session['last_activity']=now.isoformat()
    return user
def department_role(user:User, department_id:uuid.UUID, s:Session):
    if user.is_system_admin: return 'system_admin'
    row=s.scalar(select(Membership).where(Membership.user_id==user.id, Membership.department_id==department_id))
    if not row: raise HTTPException(404,'Department not found')
    return row.role_id
def context(request:Request, user:User=Depends(current_user), s:Session=Depends(db)):
    raw=request.headers.get('X-Department-Id') or request.session.get('department_id')
    api_department=getattr(request.state,'api_key_department_id',None)
    if api_department:
        if raw and uuid.UUID(raw)!=api_department: raise HTTPException(404,'Department not found')
        return user,api_department,'viewer'
    if not raw: raise HTTPException(400,'Select an active department')
    try: did=uuid.UUID(raw)
    except ValueError: raise HTTPException(400,'Invalid department')
    role=department_role(user,did,s); return user,did,role
def require_write(ctx=Depends(context)):
    if ctx[2] not in ('editor','department_admin','system_admin'): raise HTTPException(403,'Editor access required')
    return ctx
def serialize(obj):
    data={c.name:getattr(obj,c.name) for c in obj.__table__.columns}
    for k,v in data.items():
        if isinstance(v,uuid.UUID): data[k]=str(v)
        elif isinstance(v,datetime): data[k]=v.isoformat()
        elif k in ('geometry','location') and v is not None and not isinstance(v,dict):
            try: data[k]=mapping(to_shape(v))
            except Exception: data[k]=None
    return data
def geometry_value(value):
    if value is None: return None
    if not isinstance(value,dict) or value.get('type') not in ('Point','LineString','Polygon'): raise HTTPException(422,'geometry must be a GeoJSON Point, LineString or Polygon')
    try:
        geom=shape(value)
        if geom.is_empty or not geom.is_valid: raise ValueError()
        for x,y in geom.get_coordinates():
            if not (-180<=x<=180 and -90<=y<=90): raise ValueError()
    except Exception: raise HTTPException(422,'Invalid geometry or coordinate range')
    return from_shape(geom,srid=4326) if engine.dialect.name=='postgresql' else mapping(geom)

@app.on_event('startup')
def startup():
    with SessionLocal() as s:
        for code,name in [('viewer','Viewer'),('editor','Editor'),('department_admin','Department Administrator'),('system_admin','System Administrator')]:
            if not s.get(Role,code): s.add(Role(id=code,name=name))
        s.commit()

@app.get('/')
def root(): return {'message':'Sensor Metadata Management API is running','docs':'/docs'}
@app.post('/api/auth/login')
def login(body:Login, request:Request, s:Session=Depends(db)):
    u=s.scalar(select(User).where(User.username==body.username,User.is_active.is_(True)))
    if not u or not password_hash.verify(body.password,u.password_hash): raise HTTPException(401,'Invalid username or password')
    request.session.clear(); request.session['uid']=str(u.id)
    memberships=s.scalars(select(Membership).where(Membership.user_id==u.id)).all()
    if memberships: request.session['department_id']=str(memberships[0].department_id)
    return me(u,s)
@app.post('/api/auth/logout')
def logout(request:Request): request.session.clear(); return {'ok':True}
@app.get('/api/auth/me')
def me(user:User=Depends(current_user), s:Session=Depends(db)):
    memberships=s.scalars(select(Membership).where(Membership.user_id==user.id)).all()
    depts=[]
    for m in memberships:
        d=s.get(Department,m.department_id); depts.append({'id':str(d.id),'name':d.name,'role':m.role_id})
    if user.is_system_admin: depts=[{'id':str(d.id),'name':d.name,'role':'system_admin'} for d in s.scalars(select(Department)).all()]
    return {'id':str(user.id),'username':user.username,'display_name':user.display_name,'is_system_admin':user.is_system_admin,'departments':depts}

@app.post('/api/setup')
def setup(body:Input,s:Session=Depends(db)):
    """One-time local bootstrap. Set ALLOW_SETUP=false after creating initial admin."""
    if os.getenv('ALLOW_SETUP','true').lower()!='true' or s.scalar(select(func.count()).select_from(User)): raise HTTPException(403,'Setup is closed')
    name=body.get('username','admin'); password=body.get('password','')
    if len(password)<12: raise HTTPException(422,'Password must be at least 12 characters')
    org=Organisation(name=body.get('organisation','OrgX')); s.add(org); s.flush(); dep=Department(name=body.get('department','Water Operations'),organisation_id=org.id); s.add(dep); s.flush()
    user=User(username=name,display_name=body.get('display_name',name),password_hash=password_hash.hash(password),is_system_admin=True); s.add(user); s.flush(); s.add(Membership(user_id=user.id,department_id=dep.id,role_id='department_admin'))
    seeds={'measurement_types':['Pressure','Flow','VBat','Chlorine','Door','GPRS','Conductivity','Turbidity','Temperature','pH','Level','Pump_Status','Pump_Control','Power','Overload','Remote','UV254','PRV'],'measurement_units':['Bar','m3/h','Open/Close','Off/On','V (bin12Volts)'],'device_types':['S272','T56','T57','T58','T59','T60','T61','T62','T63','T64','Station'],'network_element_types':['DMA_Inlet','PipeNetwork']}
    ids={}
    for list_name,names in seeds.items():
        ids[list_name]={}
        for n in names:
            x=ListValue(list_name=list_name,name=n); s.add(x); s.flush(); ids[list_name][n]=x.id
    for type_name,value in [('Flow','ActFlow'),('Pressure','ActPrUp'),('Pressure','ActPrDwn'),('PRV','PRVClose'),('Level','Level'),('Chlorine','Chlorin')]:
        if type_name in ids['measurement_types']: s.add(LOVPair(measurement_type_id=ids['measurement_types'][type_name],value_code=value))
    s.commit()
    if body.get('demo',False):
        ne=NetworkElement(department_id=dep.id,network_element_id='NE_000062',network_element_name='DMA_XXX_Inlet',network_element_type_id=ids['network_element_types']['DMA_Inlet'],network_area='DMA_XXX'); ne2=NetworkElement(department_id=dep.id,network_element_id='NE_000235',network_element_name='DMA_XXX_Epiktitou',network_element_type_id=ids['network_element_types']['PipeNetwork'],network_area='DMA_XXX'); device=PhysicalDevice(department_id=dep.id,device_id='RTUXXX',device_name='RTUXXX',device_type_id=ids['device_types']['S272'],communication_parameters={'IP':'192.0.2.10'}); s.add_all([ne,ne2,device]); s.flush()
        for mid,n,t,unit,el,col in [('LLWD_000286','RTU45_Door','Door','Open/Close',ne,'v1'),('LLWD_000287','DMA_XXX_Inlet_Flow','Flow','m3/h',ne,'v2')]:
            measurement=Measurement(department_id=dep.id,measurement_id=mid,measurement_name=n,measurement_collection_batch=3,measurement_type_id=ids['measurement_types'][t],measurement_unit_id=ids['measurement_units'][unit],network_element_id=el.id,physical_device_id=device.id); s.add(measurement); s.flush(); s.add(DataCollectionConfiguration(measurement_id=measurement.id,collection_type='ODBC',parameters={'DB':'LLDB','Table':'T54','Column':col}))
        device2=PhysicalDevice(department_id=dep.id,device_id='RTUXX2',device_name='RTUXX2',device_type_id=ids['device_types']['S272'],communication_parameters={'IP':'192.0.2.11'}); s.add(device2); s.flush(); pressure=Measurement(department_id=dep.id,measurement_id='LLWD_000665',measurement_name='DMA_XXX_Pressure_P1(Epiktitou)',measurement_collection_batch=1,measurement_type_id=ids['measurement_types']['Pressure'],measurement_unit_id=ids['measurement_units']['Bar'],network_element_id=ne2.id,physical_device_id=device2.id); s.add(pressure); s.flush(); s.add(DataCollectionConfiguration(measurement_id=pressure.id,collection_type='IOT',parameters={'Provider':'ExampleIoT','URL':'https://example.invalid/channels/','Channel':'C5'}))
        s.commit()
    return {'created':True,'username':name,'department_id':str(dep.id)}

ENTITY_MAP={'measurements':Measurement,'physical-devices':PhysicalDevice,'network-elements':NetworkElement}
ENTITY_FIELDS={Measurement:{'measurement_id','measurement_name','measurement_collection_batch','measurement_type_id','measurement_unit_id','meter_account','customer_account','details','network_element_id','physical_device_id','collection_type','collection_parameters'},PhysicalDevice:{'device_id','device_name','device_type_id','parent_device_id','communication_parameters','longitude','latitude','manufacturer','model','serial_number','installation_date'},NetworkElement:{'network_element_id','network_element_name','network_element_type_id','network_area','latitude','longitude','elevation','strategic_id','dma_id','address','network_element_code','district','geometry'}}
def contains_secret_key(value):
    if isinstance(value,dict):
        for key,item in value.items():
            compact=''.join(ch for ch in str(key).lower() if ch.isalnum())
            if compact in {'credentialskeys','credential','credentials','password','secret','apikey','accesstoken','refreshtoken'} or contains_secret_key(item): return True
    elif isinstance(value,list): return any(contains_secret_key(item) for item in value)
    return False
def list_for(cls,did,s,q,page,page_size,active,filters=None,sort_by=None,sort_order='asc'):
    stmt=select(cls).where(cls.department_id==did)
    if active is not None: stmt=stmt.where(cls.is_active==active)
    filters=filters or {}
    if cls is Measurement:
        for key in ('measurement_type_id','measurement_unit_id','physical_device_id','network_element_id'):
            if filters.get(key):
                try: stmt=stmt.where(getattr(cls,key)==uuid.UUID(str(filters[key])))
                except ValueError: raise HTTPException(422,{'field':key,'reason':'Must be a UUID'})
        if filters.get('batch') is not None: stmt=stmt.where(cls.measurement_collection_batch==filters['batch'])
        if filters.get('network_area'): stmt=stmt.where(cls.network_element_id.in_(select(NetworkElement.id).where(NetworkElement.network_area.ilike(f"%{filters['network_area']}%"))))
        if filters.get('collection_type'): stmt=stmt.where(cls.id.in_(select(DataCollectionConfiguration.measurement_id).where(DataCollectionConfiguration.collection_type==filters['collection_type'])))
    elif cls is PhysicalDevice and filters.get('device_type_id'):
        try: stmt=stmt.where(cls.device_type_id==uuid.UUID(str(filters['device_type_id'])))
        except ValueError: raise HTTPException(422,{'field':'device_type_id','reason':'Must be a UUID'})
    elif cls is NetworkElement:
        if filters.get('network_area'): stmt=stmt.where(cls.network_area.ilike(f"%{filters['network_area']}%"))
        if filters.get('network_element_type_id'):
            try: stmt=stmt.where(cls.network_element_type_id==uuid.UUID(str(filters['network_element_type_id'])))
            except ValueError: raise HTTPException(422,{'field':'network_element_type_id','reason':'Must be a UUID'})
    if q:
        fields=[getattr(cls,n) for n in ('measurement_id','measurement_name','device_id','device_name','network_element_id','network_element_name') if hasattr(cls,n)]
        stmt=stmt.where(__import__('sqlalchemy').or_(*(f.ilike(f'%{q}%') for f in fields)))
    total=s.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    allowed={Measurement:['measurement_id','measurement_name','measurement_collection_batch','measurement_type','measurement_unit','collection_type','is_active','updated_at','created_at'],PhysicalDevice:['device_id','device_name','device_type','is_active','updated_at','created_at'],NetworkElement:['network_element_id','network_element_name','network_element_type','network_area','is_active','updated_at','created_at']}
    sort=sort_by if sort_by in allowed[cls] else 'updated_at'
    if cls is Measurement and sort in ('measurement_type','measurement_unit'):
        list_id=Measurement.measurement_type_id if sort=='measurement_type' else Measurement.measurement_unit_id
        sort_col=select(ListValue.name).where(ListValue.id==list_id).scalar_subquery()
    elif cls is Measurement and sort=='collection_type': sort_col=select(DataCollectionConfiguration.collection_type).where(DataCollectionConfiguration.measurement_id==Measurement.id).scalar_subquery()
    elif cls is PhysicalDevice and sort=='device_type': sort_col=select(ListValue.name).where(ListValue.id==PhysicalDevice.device_type_id).scalar_subquery()
    elif cls is NetworkElement and sort=='network_element_type': sort_col=select(ListValue.name).where(ListValue.id==NetworkElement.network_element_type_id).scalar_subquery()
    else: sort_col=getattr(cls,sort)
    sort_col=sort_col.desc() if sort_order.lower()=='desc' else sort_col.asc()
    rows=s.scalars(stmt.order_by(sort_col).offset((page-1)*page_size).limit(page_size)).all()
    items=[]
    for x in rows:
        item=serialize(x)
        if cls is Measurement:
            config=s.scalar(select(DataCollectionConfiguration).where(DataCollectionConfiguration.measurement_id==x.id))
            item.update({'measurement_type':listname(s,x.measurement_type_id),'measurement_unit':listname(s,x.measurement_unit_id),'network_element':s.get(NetworkElement,x.network_element_id).network_element_name if s.get(NetworkElement,x.network_element_id) else None,'physical_device':s.get(PhysicalDevice,x.physical_device_id).device_name if s.get(PhysicalDevice,x.physical_device_id) else None,'collection_type':config.collection_type if config else None,'collection_parameters':config.parameters if config else None})
        elif cls is PhysicalDevice: item['device_type']=listname(s,x.device_type_id)
        elif cls is NetworkElement: item['network_element_type']=listname(s,x.network_element_type_id)
        items.append(item)
    return {'items':items,'total':total,'page':page,'page_size':page_size}
def normalize_parent_device(data,did,s,exclude_id=None):
    if not data.get('parent_device_id'): return
    value=str(data['parent_device_id'])
    try: parent=s.get(PhysicalDevice,uuid.UUID(value))
    except ValueError: parent=s.scalar(select(PhysicalDevice).where(PhysicalDevice.department_id==did,PhysicalDevice.device_id==value))
    if not parent or parent.department_id!=did or not parent.is_active: raise HTTPException(422,{'field':'parent_device_id','reason':'Parent must be active and in this department'})
    seen=set(); cursor=parent
    while cursor:
        if cursor.id==exclude_id or cursor.id in seen: raise HTTPException(422,{'field':'parent_device_id','reason':'Device parent cycles are not allowed'})
        seen.add(cursor.id); cursor=s.get(PhysicalDevice,cursor.parent_device_id) if cursor.parent_device_id else None
    data['parent_device_id']=parent.id
@app.get('/api/{entity}')
def list_entities(entity:str,ctx=Depends(context),s:Session=Depends(db),page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=500),search:str='',active:bool|None=None,sort_by:str|None=None,sort_order:str='asc',measurement_type_id:str|None=None,measurement_unit_id:str|None=None,measurement_collection_batch:int|None=None,physical_device_id:str|None=None,network_element_id:str|None=None,network_area:str|None=None,collection_type:str|None=None,device_type_id:str|None=None,network_element_type_id:str|None=None):
    cls=ENTITY_MAP.get(entity)
    if not cls: raise HTTPException(404,'Not found')
    filters={'measurement_type_id':measurement_type_id,'measurement_unit_id':measurement_unit_id,'batch':measurement_collection_batch,'physical_device_id':physical_device_id,'network_element_id':network_element_id,'network_area':network_area,'collection_type':collection_type,'device_type_id':device_type_id,'network_element_type_id':network_element_type_id}
    return list_for(cls,ctx[1],s,search,page,page_size,active,filters,sort_by,sort_order)
@app.post('/api/{entity}')
def create_entity(entity:str,body:Input,ctx=Depends(require_write),s:Session=Depends(db)):
    cls=ENTITY_MAP.get(entity)
    if not cls: raise HTTPException(404,'Not found')
    raw_data=body.model_dump(exclude_unset=True); data={k:v for k,v in raw_data.items() if k in ENTITY_FIELDS[cls]}; collection_type=data.pop('collection_type',None); collection_parameters=data.pop('collection_parameters',None); data['department_id']=ctx[1]; data['created_by']=ctx[0].username
    if collection_parameters is not None and not isinstance(collection_parameters,dict): raise HTTPException(422,{'field':'collection_parameters','reason':'Must be a JSON object'})
    if collection_type is not None and not isinstance(collection_type,str): raise HTTPException(422,{'field':'collection_type','reason':'Must be text'})
    required={Measurement:['measurement_id','measurement_name','measurement_type_id','measurement_unit_id','network_element_id','physical_device_id'],PhysicalDevice:['device_id','device_name','device_type_id'],NetworkElement:['network_element_id','network_element_name','network_element_type_id']}[cls]
    missing=[key for key in required if not data.get(key)]
    if missing: raise HTTPException(422,{'field':missing[0],'reason':'Required field is missing'})
    for key in ('measurement_id','measurement_name','device_id','device_name','network_element_id','network_element_name'):
        if key in data and isinstance(data[key],str) and not data[key].strip(): raise HTTPException(422,{'field':key,'reason':'Must not be blank'})
    if 'measurement_collection_batch' in data and data['measurement_collection_batch'] is not None:
        try: data['measurement_collection_batch']=int(data['measurement_collection_batch'])
        except (ValueError,TypeError): raise HTTPException(422,{'field':'measurement_collection_batch','reason':'Must be an integer'})
    for key in ('latitude','longitude','elevation'):
        if key in data and data[key] is not None:
            try: data[key]=float(data[key])
            except (ValueError,TypeError): raise HTTPException(422,{'field':key,'reason':'Must be numeric'})
    for key in ('communication_parameters','collection_parameters'):
        value=data.get(key)
        if value is not None and not isinstance(value,dict): raise HTTPException(422,{'field':key,'reason':'Must be a JSON object'})
        if contains_secret_key(value): raise HTTPException(422,{'field':key,'reason':'Secret fields cannot be stored in metadata JSON; use an external secret manager'})
    for key,lst in [('measurement_type_id','measurement_types'),('measurement_unit_id','measurement_units'),('device_type_id','device_types'),('network_element_type_id','network_element_types')]:
        if key in data and isinstance(data[key],str) and not data[key].startswith('00000000'):
            try: data[key]=uuid.UUID(data[key])
            except ValueError: raise HTTPException(422,{'field':key,'reason':'Must contain a valid controlled list ID'})
        if key in data:
            v=s.get(ListValue,data[key]);
            if not v or v.list_name!=lst or not v.is_active: raise HTTPException(422,f'Invalid {key}')
    if cls is Measurement:
        for fk,model in [('physical_device_id',PhysicalDevice),('network_element_id',NetworkElement)]:
            try: data[fk]=uuid.UUID(str(data[fk]))
            except ValueError: raise HTTPException(422,{'field':fk,'reason':'Must contain a valid related record ID'})
            obj=s.get(model,data[fk]);
            if not obj or obj.department_id!=ctx[1] or not obj.is_active: raise HTTPException(422,f'{fk} must reference an active record in this department')
    if cls is PhysicalDevice:
        normalize_parent_device(data,ctx[1],s)
        for k in ('latitude','longitude'):
            if data.get(k) is not None and not (-90<=data[k]<=90 if k=='latitude' else -180<=data[k]<=180): raise HTTPException(422,f'{k} outside valid WGS84 range')
        ip=(data.get('communication_parameters') or {}).get('IP')
        if ip:
            import ipaddress
            try: ipaddress.ip_address(ip)
            except ValueError: raise HTTPException(422,'communication_parameters.IP must be a valid IP address')
        if (data.get('latitude') is None) != (data.get('longitude') is None): raise HTTPException(422,'Latitude and longitude must both be provided or both be empty')
        data['location']=geometry_value({'type':'Point','coordinates':[data['longitude'],data['latitude']]}) if data.get('latitude') is not None else None
    if cls is NetworkElement:
        if (data.get('latitude') is None) != (data.get('longitude') is None): raise HTTPException(422,'Latitude and longitude must both be provided or both be empty')
        if 'geometry' in data:
            raw=data['geometry']
            if raw is not None and not isinstance(raw,dict): raise HTTPException(422,'geometry must be a GeoJSON object')
            if raw and raw.get('type')=='Point':
                lon,lat=raw['coordinates'][:2]
                if data.get('latitude') is not None and (float(data['latitude'])!=float(lat) or float(data['longitude'])!=float(lon)): raise HTTPException(422,'Point geometry must match Latitude and Longitude')
                data['latitude']=lat; data['longitude']=lon
            elif raw and (data.get('latitude') is not None or data.get('longitude') is not None): raise HTTPException(422,'Latitude and Longitude spreadsheet fields are only for point geometry')
            else: data['latitude']=None; data['longitude']=None
            data['geometry']=geometry_value(raw)
        elif data.get('latitude') is not None: data['geometry']=geometry_value({'type':'Point','coordinates':[data['longitude'],data['latitude']]})
    try:
        x=cls(**data); s.add(x); s.flush()
        if cls is Measurement and (collection_type is not None or collection_parameters is not None): s.add(DataCollectionConfiguration(measurement_id=x.id,collection_type=collection_type,parameters=collection_parameters))
        s.add(ActivityLog(actor=ctx[0].username,department_id=ctx[1],action='created',entity=entity,entity_id=str(x.id))); s.commit(); s.refresh(x); return serialize(x)
    except IntegrityError as e: s.rollback(); raise HTTPException(409,'Duplicate identifier or invalid relationship') from e
@app.get('/api/{entity}/{record_id}')
def get_entity(entity:str,record_id:uuid.UUID,ctx=Depends(context),s:Session=Depends(db)):
    cls=ENTITY_MAP.get(entity); obj=s.get(cls,record_id) if cls else None
    if not obj or obj.department_id!=ctx[1]: raise HTTPException(404,'Record not found')
    result=serialize(obj)
    if cls is Measurement:
        config=s.scalar(select(DataCollectionConfiguration).where(DataCollectionConfiguration.measurement_id==obj.id)); ne=s.get(NetworkElement,obj.network_element_id); device=s.get(PhysicalDevice,obj.physical_device_id)
        result.update({'measurement_type':listname(s,obj.measurement_type_id),'measurement_unit':listname(s,obj.measurement_unit_id),'network_element':serialize(ne),'physical_device':serialize(device),'collection_type':config.collection_type if config else None,'collection_parameters':config.parameters if config else None,'Department':s.get(Department,obj.department_id).name,'MeasurementId':obj.measurement_id,'MeasurementName':obj.measurement_name,'MeasurementCollectionBatch':obj.measurement_collection_batch,'MeasurementType':listname(s,obj.measurement_type_id),'Meter Account':obj.meter_account,'Customer Account':obj.customer_account,'MeasurementUnit':listname(s,obj.measurement_unit_id),'Details':obj.details,'NetworkElementId':ne.network_element_id,'NetworkElementName':ne.network_element_name,'NetworkElementType':listname(s,ne.network_element_type_id),'NetworkArea':ne.network_area,'Latitude':ne.latitude,'Longitude':ne.longitude,'Elevation':ne.elevation,'StrategicId':ne.strategic_id,'DMAId':ne.dma_id,'PhysicalDeviceId':device.device_id,'SensorStationName':device.device_name,'SensorStationType':listname(s,device.device_type_id),'DataCollectionType':config.collection_type if config else None,'DataCollectionParameters':config.parameters if config else None})
    return result
@app.patch('/api/{entity}/{record_id}')
def update_entity(entity:str,record_id:uuid.UUID,body:Input,ctx=Depends(require_write),s:Session=Depends(db)):
    cls=ENTITY_MAP.get(entity); obj=s.get(cls,record_id) if cls else None
    if not obj or obj.department_id!=ctx[1]: raise HTTPException(404,'Record not found')
    raw_changes=body.model_dump(exclude_unset=True); changes={k:v for k,v in raw_changes.items() if k in ENTITY_FIELDS[cls]}; collection_type=changes.pop('collection_type',None); collection_parameters=changes.pop('collection_parameters',None)
    if collection_parameters is not None and not isinstance(collection_parameters,dict): raise HTTPException(422,{'field':'collection_parameters','reason':'Must be a JSON object'})
    if contains_secret_key(collection_parameters): raise HTTPException(422,{'field':'collection_parameters','reason':'Secret fields cannot be stored in metadata JSON; use an external secret manager'})
    if contains_secret_key(changes.get('communication_parameters')): raise HTTPException(422,{'field':'communication_parameters','reason':'Secret fields cannot be stored in metadata JSON; use an external secret manager'})
    for key in ('latitude','longitude','elevation'):
        if key in changes and changes[key] is not None:
            try: changes[key]=float(changes[key])
            except (ValueError,TypeError): raise HTTPException(422,{'field':key,'reason':'Must be numeric'})
    if cls is PhysicalDevice and 'parent_device_id' in changes: normalize_parent_device(changes,ctx[1],s,obj.id)
    if cls is Measurement:
        for fk,model in [('physical_device_id',PhysicalDevice),('network_element_id',NetworkElement)]:
            if fk in changes:
                try: changes[fk]=uuid.UUID(str(changes[fk]))
                except ValueError: raise HTTPException(422,{'field':fk,'reason':'Must contain a valid related record ID'})
                related=s.get(model,changes[fk])
                if not related or related.department_id!=ctx[1] or not related.is_active: raise HTTPException(422,{'field':fk,'reason':'Related record must be active and in this department'})
    for key,lst in [('measurement_type_id','measurement_types'),('measurement_unit_id','measurement_units'),('device_type_id','device_types'),('network_element_type_id','network_element_types')]:
        if key in changes:
            try: changes[key]=uuid.UUID(str(changes[key]))
            except ValueError: raise HTTPException(422,{'field':key,'reason':'Must contain a valid controlled list ID'})
            val=s.get(ListValue,changes[key])
            if not val or val.list_name!=lst: raise HTTPException(422,{'field':key,'reason':'Invalid controlled list value'})
    if cls is PhysicalDevice:
        lat=changes.get('latitude',obj.latitude); lon=changes.get('longitude',obj.longitude)
        if (lat is None)!=(lon is None): raise HTTPException(422,'Latitude and longitude must both be provided or both be empty')
        if lat is not None and not -90<=lat<=90 or lon is not None and not -180<=lon<=180: raise HTTPException(422,'Coordinates outside WGS84 bounds')
        if 'latitude' in changes or 'longitude' in changes: changes['location']=geometry_value({'type':'Point','coordinates':[lon,lat]}) if lat is not None else None
        params=changes.get('communication_parameters',obj.communication_parameters) or {}; ip=params.get('IP')
        if ip:
            import ipaddress
            try: ipaddress.ip_address(ip)
            except ValueError: raise HTTPException(422,'communication_parameters.IP must be a valid IP address')
    if cls is NetworkElement:
        lat=changes.get('latitude',obj.latitude); lon=changes.get('longitude',obj.longitude)
        if (lat is None)!=(lon is None): raise HTTPException(422,'Latitude and longitude must both be provided or both be empty')
        if lat is not None and not -90<=lat<=90 or lon is not None and not -180<=lon<=180: raise HTTPException(422,'Coordinates outside WGS84 bounds')
        if 'geometry' in changes:
            raw=changes['geometry']
            if raw is not None and not isinstance(raw,dict): raise HTTPException(422,'geometry must be a GeoJSON object')
            if raw and raw.get('type')=='Point':
                point_lon,point_lat=raw['coordinates'][:2]
                if ('latitude' in changes or 'longitude' in changes) and (float(lat)!=float(point_lat) or float(lon)!=float(point_lon)): raise HTTPException(422,'Point geometry must match Latitude and Longitude')
                changes['latitude']=point_lat; changes['longitude']=point_lon
            elif raw and ('latitude' in changes or 'longitude' in changes): raise HTTPException(422,'Latitude and Longitude fields are only for point geometry')
            else: changes['latitude']=None; changes['longitude']=None
            changes['geometry']=geometry_value(raw)
        elif 'latitude' in changes or 'longitude' in changes: changes['geometry']=geometry_value({'type':'Point','coordinates':[lon,lat]}) if lat is not None else None
    for k,v in changes.items(): setattr(obj,k,v)
    obj.updated_by=ctx[0].username
    if cls is Measurement and ('collection_type' in body.model_fields_set or 'collection_parameters' in body.model_fields_set):
        config=s.scalar(select(DataCollectionConfiguration).where(DataCollectionConfiguration.measurement_id==obj.id))
        if not config: config=DataCollectionConfiguration(measurement_id=obj.id); s.add(config)
        if 'collection_type' in body.model_fields_set: config.collection_type=collection_type
        if 'collection_parameters' in body.model_fields_set: config.parameters=collection_parameters
    s.add(ActivityLog(actor=ctx[0].username,department_id=ctx[1],action='updated',entity=entity,entity_id=str(obj.id)))
    try: s.commit(); return serialize(obj)
    except IntegrityError: s.rollback(); raise HTTPException(409,'Update conflicts with a unique identifier or relationship')
@app.delete('/api/{entity}/{record_id}',status_code=204)
def deactivate_entity(entity:str,record_id:uuid.UUID,ctx=Depends(require_write),s:Session=Depends(db)):
    cls=ENTITY_MAP.get(entity); obj=s.get(cls,record_id) if cls else None
    if not obj or obj.department_id!=ctx[1]: raise HTTPException(404,'Record not found')
    if cls is PhysicalDevice and (s.scalar(select(func.count()).select_from(Measurement).where(Measurement.physical_device_id==obj.id,Measurement.is_active.is_(True))) or s.scalar(select(func.count()).select_from(PhysicalDevice).where(PhysicalDevice.parent_device_id==obj.id,PhysicalDevice.is_active.is_(True)))): raise HTTPException(409,'Active child records prevent inactivation')
    if cls is NetworkElement and s.scalar(select(func.count()).select_from(Measurement).where(Measurement.network_element_id==obj.id,Measurement.is_active.is_(True))): raise HTTPException(409,'Active measurements prevent inactivation')
    obj.is_active=False; obj.updated_by=ctx[0].username; s.add(ActivityLog(actor=ctx[0].username,department_id=ctx[1],action='inactivated',entity=entity,entity_id=str(obj.id))); s.commit(); return Response(status_code=204)

@app.get('/api/lists')
def get_lists(user:User=Depends(current_user),s:Session=Depends(db)):
    return {name:[serialize(x) for x in s.scalars(select(ListValue).where(ListValue.list_name==name).order_by(ListValue.name))] for name in ['measurement_types','measurement_units','device_types','network_element_types']}
@app.post('/api/lists/{list_name}')
def create_list(list_name:str,body:Input,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    if list_name not in ('measurement_types','measurement_units','device_types','network_element_types'): raise HTTPException(404,'List not found')
    x=ListValue(list_name=list_name,name=body['name'],code=body.get('code'),description=body.get('description'),standard_uri=body.get('standard_uri')); s.add(x)
    try: s.commit(); s.refresh(x); return serialize(x)
    except IntegrityError: s.rollback(); raise HTTPException(409,'Controlled value already exists in this shared list')
@app.get('/api/lov-pairs')
def list_lov_pairs(user:User=Depends(current_user),s:Session=Depends(db)):
    values=[]
    for item in s.scalars(select(LOVPair).where(LOVPair.is_active.is_(True))): values.append({**serialize(item),'measurement_type':listname(s,item.measurement_type_id)})
    return values
@app.post('/api/lov-pairs')
def create_lov_pair(body:Input,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    try: type_id=uuid.UUID(str(body.get('measurement_type_id')))
    except (ValueError,TypeError): raise HTTPException(422,'measurement_type_id must be a controlled measurement type ID')
    item=s.get(ListValue,type_id)
    if not item or item.list_name!='measurement_types' or not item.is_active or not body.get('value_code'): raise HTTPException(422,'Active measurement type and value_code are required')
    row=LOVPair(measurement_type_id=type_id,value_code=body.get('value_code'),description=body.get('description')); s.add(row)
    try: s.commit(); s.refresh(row); return serialize(row)
    except IntegrityError: s.rollback(); raise HTTPException(409,'LOV pair already exists')
@app.patch('/api/lists/{list_name}/{value_id}')
def update_list_value(list_name:str,value_id:uuid.UUID,body:Input,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    value=s.get(ListValue,value_id)
    if not value or value.list_name!=list_name: raise HTTPException(404,'Controlled value not found')
    for field in ('name','code','description','standard_uri'):
        if field in body.model_fields_set: setattr(value,field,body.get(field))
    try: s.commit(); return serialize(value)
    except IntegrityError: s.rollback(); raise HTTPException(409,'Controlled value conflicts with an existing shared value')
@app.delete('/api/lists/{list_name}/{value_id}',status_code=204)
def deactivate_list_value(list_name:str,value_id:uuid.UUID,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    value=s.get(ListValue,value_id)
    if not value or value.list_name!=list_name: raise HTTPException(404,'Controlled value not found')
    value.is_active=False; s.commit(); return Response(status_code=204)
@app.get('/api/organisations')
def organisations(ctx=Depends(context),s:Session=Depends(db)):
    department=s.get(Department,ctx[1]); organisation=s.get(Organisation,department.organisation_id) if department else None
    return [serialize(organisation)] if organisation else []
@app.get('/api/departments')
def departments(ctx=Depends(context),s:Session=Depends(db)):
    department=s.get(Department,ctx[1]); return [{'id':str(department.id),'name':department.name,'role':ctx[2]}] if department else []
@app.post('/api/organisations')
def create_organisation(body:Input,user:User=Depends(current_user),s:Session=Depends(db)):
    if not user.is_system_admin: raise HTTPException(403,'System administrator access required')
    if not body.get('name'): raise HTTPException(422,'Organisation name is required')
    obj=Organisation(name=body.get('name')); s.add(obj)
    try: s.commit(); s.refresh(obj); return serialize(obj)
    except IntegrityError: s.rollback(); raise HTTPException(409,'Organisation already exists')
@app.post('/api/departments')
def create_department(body:Input,user:User=Depends(current_user),s:Session=Depends(db)):
    if not user.is_system_admin: raise HTTPException(403,'System administrator access required')
    org=s.get(Organisation,uuid.UUID(str(body.get('organisation_id',''))))
    if not org: raise HTTPException(422,'Valid organisation_id is required')
    obj=Department(name=body.get('name','').strip(),organisation_id=org.id)
    if not obj.name: raise HTTPException(422,'Department name is required')
    s.add(obj)
    try: s.commit(); s.refresh(obj); return serialize(obj)
    except IntegrityError: s.rollback(); raise HTTPException(409,'Department already exists in this organisation')
@app.get('/api/users')
def list_users(ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    stmt=select(User).join(Membership,Membership.user_id==User.id).where(Membership.department_id==ctx[1])
    return [{'id':str(u.id),'username':u.username,'display_name':u.display_name,'is_active':u.is_active,'role':m.role_id} for u,m in [(u,s.scalar(select(Membership).where(Membership.user_id==u.id,Membership.department_id==ctx[1]))) for u in s.scalars(stmt)]]
@app.post('/api/users')
def create_user(body:Input,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    if not body.get('username') or not body.get('password') or len(body.get('password'))<12: raise HTTPException(422,'Username and a password of at least 12 characters are required')
    role=body.get('role','viewer')
    if role not in ('viewer','editor','department_admin'): raise HTTPException(422,'Invalid department role')
    u=User(username=body.get('username'),display_name=body.get('display_name',body.get('username')),password_hash=password_hash.hash(body.get('password'))); s.add(u)
    try:
        s.flush(); s.add(Membership(user_id=u.id,department_id=ctx[1],role_id=role)); s.commit(); return {'id':str(u.id),'username':u.username,'role':role}
    except IntegrityError: s.rollback(); raise HTTPException(409,'Username already exists')
@app.post('/api/users/{user_id}/memberships')
def add_membership(user_id:uuid.UUID,body:Input,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    target=uuid.UUID(str(body.get('department_id',ctx[1]))) if ctx[2]=='system_admin' else ctx[1]
    user=s.get(User,user_id); department=s.get(Department,target); role=body.get('role','viewer')
    if not user or not department or not department.is_active: raise HTTPException(404,'User or department not found')
    if role not in ('viewer','editor','department_admin'): raise HTTPException(422,'Invalid department role')
    if s.scalar(select(Membership).where(Membership.user_id==user_id,Membership.department_id==target)): raise HTTPException(409,'Membership already exists')
    membership=Membership(user_id=user_id,department_id=target,role_id=role); s.add(membership)
    try: s.commit(); return {'user_id':str(user_id),'department_id':str(target),'role':role}
    except IntegrityError: s.rollback(); raise HTTPException(409,'Membership already exists')
@app.patch('/api/memberships/{membership_id}')
def update_membership(membership_id:int,body:Input,ctx=Depends(context),s:Session=Depends(db)):
    membership=s.get(Membership,membership_id)
    if not membership or (membership.department_id!=ctx[1] and ctx[2]!='system_admin'): raise HTTPException(404,'Membership not found')
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    role=body.get('role')
    if role not in ('viewer','editor','department_admin'): raise HTTPException(422,'Invalid department role')
    membership.role_id=role; s.commit(); return {'membership_id':membership.id,'role':role}
@app.delete('/api/memberships/{membership_id}',status_code=204)
def remove_membership(membership_id:int,ctx=Depends(context),s:Session=Depends(db)):
    membership=s.get(Membership,membership_id)
    if not membership or (membership.department_id!=ctx[1] and ctx[2]!='system_admin'): raise HTTPException(404,'Membership not found')
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    s.delete(membership); s.commit(); return Response(status_code=204)
@app.post('/api/api-keys')
def create_api_key(body:Input,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    token='smp_'+secrets.token_urlsafe(32); expires=None
    if body.get('expires_at'):
        try: expires=datetime.fromisoformat(body.get('expires_at').replace('Z','+00:00'))
        except ValueError: raise HTTPException(422,'expires_at must be an ISO-8601 date-time')
        if expires<=datetime.now(timezone.utc): raise HTTPException(422,'expires_at must be in the future')
    obj=APIKey(department_id=ctx[1],name=body.get('name','Read-only integration'),key_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at=expires); s.add(obj); s.commit(); return {'id':str(obj.id),'name':obj.name,'token':token,'expires_at':obj.expires_at.isoformat() if obj.expires_at else None,'warning':'Copy this key now. It cannot be displayed again.'}
@app.get('/api/api-keys')
def list_api_keys(ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    return [{'id':str(x.id),'name':x.name,'created_at':x.created_at.isoformat(),'expires_at':x.expires_at.isoformat() if x.expires_at else None,'revoked':x.revoked_at is not None} for x in s.scalars(select(APIKey).where(APIKey.department_id==ctx[1]).order_by(APIKey.created_at.desc()))]
@app.post('/api/api-keys/{key_id}/revoke')
def revoke_api_key(key_id:uuid.UUID,ctx=Depends(context),s:Session=Depends(db)):
    if ctx[2] not in ('department_admin','system_admin'): raise HTTPException(403,'Department administrator access required')
    key=s.get(APIKey,key_id)
    if not key or key.department_id!=ctx[1]: raise HTTPException(404,'API key not found')
    key.revoked_at=datetime.now(timezone.utc); s.commit(); return {'revoked':True}
@app.get('/api/dashboard/summary')
def summary(ctx=Depends(context),s:Session=Depends(db)):
    did=ctx[1]; count=lambda model,where: s.scalar(select(func.count()).select_from(model).where(model.department_id==did,*where)) or 0
    by_type=[{'label':listname(s,key) or 'Unknown','count':n} for key,n in s.execute(select(Measurement.measurement_type_id,func.count()).where(Measurement.department_id==did).group_by(Measurement.measurement_type_id))]
    by_device=[{'label':listname(s,key) or 'Unknown','count':n} for key,n in s.execute(select(PhysicalDevice.device_type_id,func.count()).where(PhysicalDevice.department_id==did).group_by(PhysicalDevice.device_type_id))]
    by_area=[{'label':area or 'Unspecified','count':n} for area,n in s.execute(select(NetworkElement.network_area,func.count(Measurement.id)).join(Measurement,Measurement.network_element_id==NetworkElement.id).where(Measurement.department_id==did).group_by(NetworkElement.network_area))]
    by_collection=[{'label':label or 'Unspecified','count':n} for label,n in s.execute(select(DataCollectionConfiguration.collection_type,func.count()).join(Measurement,Measurement.id==DataCollectionConfiguration.measurement_id).where(Measurement.department_id==did).group_by(DataCollectionConfiguration.collection_type))]
    activity=[{'actor':x.actor,'action':x.action,'entity':x.entity,'entity_id':x.entity_id,'timestamp':x.created_at.isoformat()} for x in s.scalars(select(ActivityLog).where(ActivityLog.department_id==did).order_by(ActivityLog.created_at.desc()).limit(8))]
    return {'measurements':count(Measurement,[]),'active_measurements':count(Measurement,[Measurement.is_active.is_(True)]),'devices':count(PhysicalDevice,[]),'network_elements':count(NetworkElement,[]),'inactive_records':count(Measurement,[Measurement.is_active.is_(False)])+count(PhysicalDevice,[PhysicalDevice.is_active.is_(False)])+count(NetworkElement,[NetworkElement.is_active.is_(False)]),'records_without_location':count(PhysicalDevice,[PhysicalDevice.location.is_(None)])+count(NetworkElement,[NetworkElement.geometry.is_(None)]),'measurements_by_type':by_type,'devices_by_type':by_device,'measurements_by_area':by_area,'collection_type_distribution':by_collection,'recent_activity':activity}
@app.get('/api/gis/geojson')
def geojson(ctx=Depends(context),s:Session=Depends(db)):
    features=[]
    for model,idkey,namekey,typkey in [(NetworkElement,'network_element_id','network_element_name','network_element_type_id'),(PhysicalDevice,'device_id','device_name','device_type_id')]:
        for x in s.scalars(select(model).where(model.department_id==ctx[1],model.is_active.is_(True))):
            geom=(mapping(to_shape(x.geometry)) if x.geometry is not None and not isinstance(x.geometry,dict) else x.geometry) if isinstance(x,NetworkElement) else ({'type':'Point','coordinates':[x.longitude,x.latitude]} if x.latitude is not None and x.longitude is not None else None)
            if geom and (isinstance(x,NetworkElement) or x.latitude is not None): features.append({'type':'Feature','geometry':geom,'properties':{'id':str(x.id),'external_id':getattr(x,idkey),'name':getattr(x,namekey),'kind':'network_element' if isinstance(x,NetworkElement) else 'physical_device'}})
    return {'type':'FeatureCollection','features':features}

MEAS_HEADERS=['Department','MeasurementId','MeasurementName','MeasurementCollectionBatch','MeasurementType','Meter Account','Customer Account','MeasurementUnit','Details','NetworkElementId','NetworkElementName','NetworkElementType','NetworkArea','Latitude','Longitude','Elevation','StrategicId','DMAId','PhysicalDeviceId','SensorStationName','SensorStationType','DataCollectionType','DataCollectionParameters']
NE_HEADERS=['Department','NetworkElementId','NetworkElementName','NetworkElementType','NetworkArea','Latitude','Longitude','Elevation','StrategicId','DMAId','Address']
DEV_HEADERS=['Department','Device_id','Device_name','Device_type','CommunicationParameters','longitude','latitude']
def listname(s,id): return s.get(ListValue,id).name if id and s.get(ListValue,id) else None
@app.get('/api/export')
def export(ctx=Depends(context),s:Session=Depends(db)):
    try: from openpyxl import Workbook
    except ImportError: raise HTTPException(503,'Excel support unavailable')
    wb=Workbook(); wb.remove(wb.active); dep=s.get(Department,ctx[1]).name
    ws=wb.create_sheet('Measurements'); ws.append(MEAS_HEADERS)
    for x in s.scalars(select(Measurement).where(Measurement.department_id==ctx[1])):
        ne=s.get(NetworkElement,x.network_element_id); d=s.get(PhysicalDevice,x.physical_device_id)
        config=s.scalar(select(DataCollectionConfiguration).where(DataCollectionConfiguration.measurement_id==x.id))
        ws.append([dep,x.measurement_id,x.measurement_name,x.measurement_collection_batch,listname(s,x.measurement_type_id),x.meter_account,x.customer_account,listname(s,x.measurement_unit_id),x.details,ne.network_element_id,ne.network_element_name,listname(s,ne.network_element_type_id),ne.network_area,ne.latitude,ne.longitude,ne.elevation,ne.strategic_id,ne.dma_id,d.device_id,d.device_name,listname(s,d.device_type_id),config.collection_type if config else None,json.dumps(config.parameters) if config and config.parameters else None])
    ws=wb.create_sheet('NetworkElements'); ws.append(NE_HEADERS)
    for x in s.scalars(select(NetworkElement).where(NetworkElement.department_id==ctx[1])): ws.append([dep,x.network_element_id,x.network_element_name,listname(s,x.network_element_type_id),x.network_area,x.latitude,x.longitude,x.elevation,x.strategic_id,x.dma_id,x.address])
    ws=wb.create_sheet('Physical_devices'); ws.append(DEV_HEADERS)
    for x in s.scalars(select(PhysicalDevice).where(PhysicalDevice.department_id==ctx[1])): ws.append([dep,x.device_id,x.device_name,listname(s,x.device_type_id),json.dumps(x.communication_parameters) if x.communication_parameters else None,x.longitude,x.latitude])
    buf=io.BytesIO(); wb.save(buf); from fastapi.responses import StreamingResponse
    return StreamingResponse(io.BytesIO(buf.getvalue()),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="sensor-metadata.xlsx"'})
@app.get('/api/import/template')
def import_template(ctx=Depends(context)):
    from openpyxl import Workbook
    from fastapi.responses import StreamingResponse
    wb=Workbook(); ws=wb.active; ws.title='Measurements'; ws.append(MEAS_HEADERS)
    ws=wb.create_sheet('NetworkElements'); ws.append(NE_HEADERS)
    ws=wb.create_sheet('Physical_devices'); ws.append(DEV_HEADERS)
    output=io.BytesIO(); wb.save(output)
    return StreamingResponse(io.BytesIO(output.getvalue()),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="sensor-metadata-template.xlsx"'})
@app.post('/api/import/validate')
async def validate_import(file:UploadFile=File(...),ctx=Depends(require_write),s:Session=Depends(db)):
    if not file.filename.lower().endswith('.xlsx'): raise HTTPException(415,'Upload an .xlsx workbook')
    from openpyxl import load_workbook
    wb=load_workbook(io.BytesIO(await file.read()),read_only=True,data_only=True); expected={'Measurements':MEAS_HEADERS,'NetworkElements':NE_HEADERS,'Physical_devices':DEV_HEADERS}; report=[]
    did=ctx[1]; dept_name=s.get(Department,did).name
    for name in expected:
        if name not in wb.sheetnames: report.append({'sheet':name,'row':1,'column':'worksheet','error':'Required operational worksheet is missing'})
    for name,headers in expected.items():
        if name not in wb.sheetnames: continue
        ws=wb[name]; actual=[c.value for c in next(ws.iter_rows(min_row=1,max_row=1))]
        if name=='Physical_devices' and len(actual)==8 and actual[-1] is None: actual=actual[:7]
        if actual!=headers: report.append({'sheet':name,'row':1,'column':'headers','error':'Header columns/order do not match required template'})
        for rn,row in enumerate(ws.iter_rows(min_row=2,values_only=True),2):
            if not any(v is not None for v in row): continue
            if name=='Measurements' and (len(row)<23 or not row[1] or not row[2] or not row[9] or not row[18]): report.append({'sheet':name,'row':rn,'column':'required fields','error':'MeasurementId, MeasurementName, NetworkElementId and PhysicalDeviceId are required'})
            for col in (22,) if name=='Measurements' else ((4,) if name=='Physical_devices' else ()):
                if col<len(row) and row[col] is not None:
                    try:
                        if not isinstance(json.loads(row[col]),dict): raise ValueError()
                    except Exception: report.append({'sheet':name,'row':rn,'column':headers[col],'error':'Expected a JSON object'})
    lists={ln:{x.name:x for x in s.scalars(select(ListValue).where(ListValue.list_name==ln,ListValue.is_active.is_(True)))} for ln in ('measurement_types','measurement_units','device_types','network_element_types')}
    ne_rows=[]; dev_rows=[]; measurement_rows=[]
    for sheet,target in [('NetworkElements',ne_rows),('Physical_devices',dev_rows),('Measurements',measurement_rows)]:
        if sheet in wb.sheetnames:
            target.extend((n,list(row)) for n,row in enumerate(wb[sheet].iter_rows(min_row=2,values_only=True),2) if any(v is not None for v in row))
    ne_map={x.network_element_id:x for x in s.scalars(select(NetworkElement).where(NetworkElement.department_id==did,NetworkElement.is_active.is_(True)))}
    dev_map={x.device_id:x for x in s.scalars(select(PhysicalDevice).where(PhysicalDevice.department_id==did,PhysicalDevice.is_active.is_(True)))}
    m_ids={x.measurement_id for x in s.scalars(select(Measurement).where(Measurement.department_id==did))}
    def check_department(sheet,rn,value):
        if value is not None and str(value)!=dept_name: report.append({'sheet':sheet,'row':rn,'column':'Department','error':'Row department differs from selected department'})
    def valid_coords(sheet,rn,lat,lon):
        try:
            if (lat is None)!=(lon is None): raise ValueError()
            if lat is not None and (not -90<=float(lat)<=90 or not -180<=float(lon)<=180): raise ValueError()
        except (ValueError,TypeError): report.append({'sheet':sheet,'row':rn,'column':'Latitude/Longitude','error':'Provide both coordinates in valid WGS84 ranges, or leave both empty'})
    new_ne_ids=set()
    for rn,r in ne_rows:
        r=(r+[None]*11)[:11]; department,nid,name,typ,area,lat,lon,elev,strat,dma,address=r; check_department('NetworkElements',rn,department)
        if not nid or not name or typ not in lists['network_element_types']: report.append({'sheet':'NetworkElements','row':rn,'column':'required fields','error':'NetworkElementId, NetworkElementName and active NetworkElementType are required'}); continue
        if not isinstance(nid,str): report.append({'sheet':'NetworkElements','row':rn,'column':'NetworkElementId','error':'IDs must be stored as text to preserve leading zeroes'})
        if nid in ne_map or nid in new_ne_ids: report.append({'sheet':'NetworkElements','row':rn,'column':'NetworkElementId','error':f'Duplicate NetworkElementId {nid}'})
        new_ne_ids.add(str(nid)); valid_coords('NetworkElements',rn,lat,lon)
        ne_map[str(nid)]={'network_element_name':name,'network_element_type_id':lists['network_element_types'].get(typ).id if typ in lists['network_element_types'] else None,'network_area':area,'latitude':lat,'longitude':lon,'elevation':elev,'strategic_id':str(strat) if strat is not None else None,'dma_id':str(dma) if dma is not None else None}
    new_dev_ids=set()
    for rn,r in dev_rows:
        r=(r+[None]*7)[:7]; department,device_id,name,typ,comm,lon,lat=r; check_department('Physical_devices',rn,department)
        if not device_id or not name or typ not in lists['device_types']: report.append({'sheet':'Physical_devices','row':rn,'column':'required fields','error':'Device_id, Device_name and active Device_type are required'}); continue
        if not isinstance(device_id,str): report.append({'sheet':'Physical_devices','row':rn,'column':'Device_id','error':'IDs must be stored as text to preserve leading zeroes'})
        if device_id in dev_map or device_id in new_dev_ids: report.append({'sheet':'Physical_devices','row':rn,'column':'Device_id','error':f'Duplicate Device_id {device_id}'})
        new_dev_ids.add(str(device_id)); valid_coords('Physical_devices',rn,lat,lon)
        try: params=json.loads(comm) if isinstance(comm,str) and comm.strip() else comm
        except Exception: params=None
        if params is not None and not isinstance(params,dict): report.append({'sheet':'Physical_devices','row':rn,'column':'CommunicationParameters','error':'Expected a JSON object'})
        if contains_secret_key(params): report.append({'sheet':'Physical_devices','row':rn,'column':'CommunicationParameters','error':'Secret values cannot be stored in workbook JSON'})
        ip=(params or {}).get('IP') if isinstance(params,dict) else None
        if ip:
            import ipaddress
            try: ipaddress.ip_address(ip)
            except ValueError: report.append({'sheet':'Physical_devices','row':rn,'column':'CommunicationParameters','error':'IP must be a valid address'})
        dev_map[str(device_id)]={'device_name':name,'device_type_id':lists['device_types'].get(typ).id if typ in lists['device_types'] else None}
    for rn,r in measurement_rows:
        r=(r+[None]*23)[:23]; (department,mid,name,batch,mtype,meter,customer,unit,details,neid,nename,netype,area,lat,lon,elev,strat,dma,devid,dname,dtype,ctype,params)=r; check_department('Measurements',rn,department)
        if not mid or not name or not neid or not devid or mtype not in lists['measurement_types'] or unit not in lists['measurement_units']: report.append({'sheet':'Measurements','row':rn,'column':'required fields','error':'IDs, name, active MeasurementType and MeasurementUnit, NetworkElementId and PhysicalDeviceId are required'}); continue
        if not isinstance(mid,str): report.append({'sheet':'Measurements','row':rn,'column':'MeasurementId','error':'IDs must be stored as text to preserve leading zeroes'})
        if mid in m_ids: report.append({'sheet':'Measurements','row':rn,'column':'MeasurementId','error':f'Duplicate MeasurementId {mid}'})
        m_ids.add(str(mid)); ne=ne_map.get(str(neid)); dev=dev_map.get(str(devid))
        if not ne: report.append({'sheet':'Measurements','row':rn,'column':'NetworkElementId','error':'Network element does not resolve in this department or workbook'})
        if not dev: report.append({'sheet':'Measurements','row':rn,'column':'PhysicalDeviceId','error':'Physical device does not resolve in this department or workbook'})
        if ne:
            actual={'NetworkElementName':ne.network_element_name if isinstance(ne,NetworkElement) else ne['network_element_name'],'NetworkElementType':listname(s,ne.network_element_type_id) if isinstance(ne,NetworkElement) else (next((x.name for x in lists['network_element_types'].values() if x.id==ne['network_element_type_id']),None)),'NetworkArea':ne.network_area if isinstance(ne,NetworkElement) else ne['network_area'],'Latitude':ne.latitude if isinstance(ne,NetworkElement) else ne['latitude'],'Longitude':ne.longitude if isinstance(ne,NetworkElement) else ne['longitude'],'Elevation':ne.elevation if isinstance(ne,NetworkElement) else ne['elevation'],'StrategicId':ne.strategic_id if isinstance(ne,NetworkElement) else ne['strategic_id'],'DMAId':ne.dma_id if isinstance(ne,NetworkElement) else ne['dma_id']}
            for col,value in [('NetworkElementName',nename),('NetworkElementType',netype),('NetworkArea',area),('Latitude',lat),('Longitude',lon),('Elevation',elev),('StrategicId',strat),('DMAId',dma)]:
                if value is not None and str(value)!=str(actual[col]): report.append({'sheet':'Measurements','row':rn,'column':col,'error':f'Provided value differs from referenced network element ({actual[col]!r})'})
        if dev:
            expected_name=dev.device_name if isinstance(dev,PhysicalDevice) else dev['device_name']; expected_type=listname(s,dev.device_type_id) if isinstance(dev,PhysicalDevice) else next((x.name for x in lists['device_types'].values() if x.id==dev['device_type_id']),None)
            if dname is not None and str(dname)!=str(expected_name): report.append({'sheet':'Measurements','row':rn,'column':'SensorStationName','error':'Provided value differs from referenced device'})
            if dtype is not None and str(dtype)!=str(expected_type): report.append({'sheet':'Measurements','row':rn,'column':'SensorStationType','error':'Provided value differs from referenced device'})
        if params is not None:
            try: parsed=json.loads(params) if isinstance(params,str) else params
            except Exception: parsed=None
            if not isinstance(parsed,dict): report.append({'sheet':'Measurements','row':rn,'column':'DataCollectionParameters','error':'Expected a JSON object'})
            elif contains_secret_key(parsed): report.append({'sheet':'Measurements','row':rn,'column':'DataCollectionParameters','error':'Secret values cannot be stored in workbook JSON'})
    return {'valid':not report,'rows':{n:max(0,wb[n].max_row-1) for n in expected if n in wb.sheetnames},'errors':report,'message':'Preview validation completed. Commit endpoint only accepts validated records.'}

@app.post('/api/import/commit')
async def commit_import(file:UploadFile=File(...),ctx=Depends(require_write),s:Session=Depends(db)):
    """Validate and atomically insert workbook rows. Conflicts are rejected; imports never overwrite."""
    if not file.filename.lower().endswith('.xlsx'): raise HTTPException(415,'Upload an .xlsx workbook')
    from openpyxl import load_workbook
    wb=load_workbook(io.BytesIO(await file.read()),read_only=True,data_only=True); did=ctx[1]; errors=[]
    dept_name=s.get(Department,did).name
    expected={'Measurements':MEAS_HEADERS,'NetworkElements':NE_HEADERS,'Physical_devices':DEV_HEADERS}
    for sheet,headers in expected.items():
        if sheet not in wb.sheetnames:
            errors.append({'sheet':sheet,'row':1,'column':'worksheet','error':'Required operational worksheet is missing'}); continue
        actual=[c.value for c in next(wb[sheet].iter_rows(min_row=1,max_row=1))]
        if sheet=='Physical_devices' and len(actual)==8 and actual[-1] is None: actual=actual[:7]
        if actual!=headers: errors.append({'sheet':sheet,'row':1,'column':'headers','error':'Header columns/order do not match required template'})
    def rows(sheet):
        if sheet not in wb.sheetnames: return []
        it=wb[sheet].iter_rows(values_only=True); next(it,None); return [(i, list(r)) for i,r in enumerate(it,2) if any(v is not None for v in r)]
    ne_rows,dev_rows,meas_rows=rows('NetworkElements'),rows('Physical_devices'),rows('Measurements')
    try:
        lv={ln:{x.name:x.id for x in s.scalars(select(ListValue).where(ListValue.list_name==ln,ListValue.is_active.is_(True)))} for ln in ['measurement_types','measurement_units','device_types','network_element_types']}
        ne_by_id={x.network_element_id:x for x in s.scalars(select(NetworkElement).where(NetworkElement.department_id==did))}
        dev_by_id={x.device_id:x for x in s.scalars(select(PhysicalDevice).where(PhysicalDevice.department_id==did))}
        new_nes={}; new_devs={}; new_measurements=[]; pending_configs=[]
        for rn,r in ne_rows:
            r=(r+[None]*11)[:11]; department_name,nid,name,typ,area,lat,lon,elev,strat,dma,address=r
            if department_name is not None and str(department_name)!=dept_name: errors.append({'sheet':'NetworkElements','row':rn,'column':'Department','error':'Row department differs from selected department'})
            if not nid or not name or typ not in lv['network_element_types']: errors.append({'sheet':'NetworkElements','row':rn,'error':'ID, name and active NetworkElementType are required'}); continue
            if str(nid) in ne_by_id or str(nid) in new_nes: errors.append({'sheet':'NetworkElements','row':rn,'error':f'Duplicate NetworkElementId {nid}'}); continue
            if lat is not None and not -90<=float(lat)<=90 or lon is not None and not -180<=float(lon)<=180: errors.append({'sheet':'NetworkElements','row':rn,'error':'Coordinates outside WGS84 bounds'}); continue
            gj={'type':'Point','coordinates':[float(lon),float(lat)]} if lat is not None and lon is not None else None
            new_nes[str(nid)]=NetworkElement(department_id=did,network_element_id=str(nid),network_element_name=str(name),network_element_type_id=lv['network_element_types'][typ],network_area=area,latitude=lat,longitude=lon,elevation=elev,strategic_id=str(strat) if strat is not None else None,dma_id=str(dma) if dma is not None else None,address=address,geometry=geometry_value(gj))
        for x in new_nes.values(): s.add(x)
        s.flush(); ne_by_id.update(new_nes)
        for rn,r in dev_rows:
            r=(r+[None]*7)[:7]; department_name,device_id,name,typ,comm,lon,lat=r
            if department_name is not None and str(department_name)!=dept_name: errors.append({'sheet':'Physical_devices','row':rn,'column':'Department','error':'Row department differs from selected department'})
            try: params=json.loads(comm) if isinstance(comm,str) and comm.strip() else comm
            except Exception: params=None; errors.append({'sheet':'Physical_devices','row':rn,'error':'CommunicationParameters must be a JSON object'})
            if params is not None and not isinstance(params,dict): errors.append({'sheet':'Physical_devices','row':rn,'error':'CommunicationParameters must be a JSON object'})
            if contains_secret_key(params): errors.append({'sheet':'Physical_devices','row':rn,'column':'CommunicationParameters','error':'Secret values cannot be stored in workbook JSON'})
            if not device_id or not name or typ not in lv['device_types']: errors.append({'sheet':'Physical_devices','row':rn,'error':'Device_id, Device_name and active Device_type are required'}); continue
            if str(device_id) in dev_by_id or str(device_id) in new_devs: errors.append({'sheet':'Physical_devices','row':rn,'error':f'Duplicate Device_id {device_id}'}); continue
            if lat is not None and not -90<=float(lat)<=90 or lon is not None and not -180<=float(lon)<=180: errors.append({'sheet':'Physical_devices','row':rn,'error':'Coordinates outside WGS84 bounds'}); continue
            ip=(params or {}).get('IP')
            if ip:
                import ipaddress
                try: ipaddress.ip_address(ip)
                except ValueError: errors.append({'sheet':'Physical_devices','row':rn,'error':'CommunicationParameters.IP must be a valid IP address'})
            location=geometry_value({'type':'Point','coordinates':[float(lon),float(lat)]}) if lat is not None and lon is not None else None
            new_devs[str(device_id)]=PhysicalDevice(department_id=did,device_id=str(device_id),device_name=str(name),device_type_id=lv['device_types'][typ],communication_parameters=params,longitude=lon,latitude=lat,location=location)
        for x in new_devs.values(): s.add(x)
        s.flush(); dev_by_id.update(new_devs)
        for rn,r in meas_rows:
            r=(r+[None]*23)[:23]; (department_name,mid,name,batch,mtype,meter,customer,unit,details,neid,nename,netype,area,lat,lon,elev,strat,dma,devid,dname,dtype,ctype,params)=r
            if department_name is not None and str(department_name)!=dept_name: errors.append({'sheet':'Measurements','row':rn,'column':'Department','error':'Row department differs from selected department'})
            try: params=json.loads(params) if isinstance(params,str) and params.strip() else params
            except Exception: params=None; errors.append({'sheet':'Measurements','row':rn,'error':'DataCollectionParameters must be a JSON object'})
            if params is not None and not isinstance(params,dict): errors.append({'sheet':'Measurements','row':rn,'error':'DataCollectionParameters must be a JSON object'})
            if contains_secret_key(params): errors.append({'sheet':'Measurements','row':rn,'column':'DataCollectionParameters','error':'Secret values cannot be stored in workbook JSON'})
            if not mid or not name or not neid or not devid or mtype not in lv['measurement_types'] or unit not in lv['measurement_units']: errors.append({'sheet':'Measurements','row':rn,'error':'Required values, active MeasurementType and active MeasurementUnit are required'}); continue
            if s.scalar(select(Measurement.id).where(Measurement.department_id==did,Measurement.measurement_id==str(mid))) or any(x.measurement_id==str(mid) for x in new_measurements): errors.append({'sheet':'Measurements','row':rn,'error':f'Duplicate MeasurementId {mid}'}); continue
            ne=ne_by_id.get(str(neid)); dev=dev_by_id.get(str(devid))
            if not ne or not dev: errors.append({'sheet':'Measurements','row':rn,'error':'NetworkElementId and PhysicalDeviceId must resolve within the selected department'}); continue
            for col,got,want in [('NetworkElementName',nename,ne.network_element_name),('NetworkElementType',netype,listname(s,ne.network_element_type_id)),('NetworkArea',area,ne.network_area),('Latitude',lat,ne.latitude),('Longitude',lon,ne.longitude),('Elevation',elev,ne.elevation),('StrategicId',strat,ne.strategic_id),('DMAId',dma,ne.dma_id),('SensorStationName',dname,dev.device_name),('SensorStationType',dtype,listname(s,dev.device_type_id))]:
                if got is not None and str(got)!=str(want): errors.append({'sheet':'Measurements','row':rn,'column':col,'error':f'Provided value {got!r} differs from related record value {want!r}'})
            measurement=Measurement(department_id=did,measurement_id=str(mid),measurement_name=str(name),measurement_collection_batch=batch,measurement_type_id=lv['measurement_types'][mtype],measurement_unit_id=lv['measurement_units'][unit],meter_account=meter,customer_account=customer,details=details,network_element_id=ne.id,physical_device_id=dev.id); new_measurements.append(measurement); pending_configs.append((measurement,ctype,params))
        if errors: s.rollback(); return {'imported':False,'errors':errors,'message':'No rows imported; fix all errors and retry.'}
        s.add_all(new_measurements); s.flush()
        s.add_all([DataCollectionConfiguration(measurement_id=m.id,collection_type=ctype,parameters=params) for m,ctype,params in pending_configs if ctype is not None or params is not None]); s.commit(); return {'imported':True,'counts':{'NetworkElements':len(new_nes),'Physical_devices':len(new_devs),'Measurements':len(new_measurements)},'message':'Workbook imported atomically.'}
    except IntegrityError as exc:
        s.rollback(); raise HTTPException(409,'Workbook conflicts with database constraints; no rows were imported') from exc
    except Exception:
        s.rollback(); raise

@app.get('/measurements')
def legacy_measurements(ctx=Depends(context),s:Session=Depends(db)):
    data=list_for(Measurement,ctx[1],s,'',1,500,None)['items']; return [{'id':x['measurement_id'],'name':x['measurement_name'],'type':listname(s,uuid.UUID(x['measurement_type_id'])),'unit':listname(s,uuid.UUID(x['measurement_unit_id']))} for x in data]
@app.post('/measurements')
def legacy_create(body:Input,ctx=Depends(require_write),s:Session=Depends(db)):
    lists={n:{x.name:x.id for x in s.scalars(select(ListValue).where(ListValue.list_name==n,ListValue.is_active.is_(True)))} for n in ['measurement_types','measurement_units']}
    if body.get('type') not in lists['measurement_types'] or body.get('unit') not in lists['measurement_units']: raise HTTPException(422,'Unknown measurement type or unit')
    data=body.model_dump(exclude_unset=True); data['measurement_name']=data.pop('measurement_name',data.pop('name',None)); data['measurement_type_id']=lists['measurement_types'][body.get('type')]; data['measurement_unit_id']=lists['measurement_units'][body.get('unit')]
    result=create_entity('measurements',Input(**data),ctx,s)
    result.update({'name':result['measurement_name'],'type':body.get('type'),'unit':body.get('unit')})
    return result

# Prefer concrete/static routes over the generic entity resource route.
app.router.routes.sort(key=lambda route: sum(1 for part in route.path.split('/') if part and '{' not in part), reverse=True)
