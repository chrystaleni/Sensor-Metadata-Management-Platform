from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Sensor Metadata Management API"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
class Measurement(BaseModel):
    name: str
    type: str
    unit: str


measurements = []


@app.get("/")
def root():
    return {
        "message": "Sensor Metadata Management API is running"
    }


@app.get("/measurements")
def get_measurements():
    return measurements


@app.post("/measurements")
def create_measurement(measurement: Measurement):
    measurements.append(measurement)
    return measurement