from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import date
from sqlalchemy import create_engine, Column, Integer, String, Date, Boolean, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from io import StringIO
import csv
import os

# -------------------------------
# DATABASE setup
# -------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./subway_rides.db")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class SubwayRide(Base):
    __tablename__ = "rides"

    id = Column(Integer, primary_key=True, index=True)
    line = Column(String, nullable=False)
    board_stop = Column(String, nullable=False)
    depart_stop = Column(String, nullable=False)
    date = Column(Date, nullable=False)
    transferred = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)

# -------------------------------
# API setup
# -------------------------------
app = FastAPI(title="NYC Subway Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace "*" with your frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RideCreate(BaseModel):
    line: str
    board_stop: str
    depart_stop: str
    date: date
    transferred: bool = False

# -------------------------------
# API Routes
# -------------------------------
@app.post("/rides/")
def create_ride(ride: RideCreate):
    ride_date = ride.date
    new_ride = SubwayRide(
        line=ride.line,
        board_stop=ride.board_stop,
        depart_stop=ride.depart_stop,
        date=ride_date,
        transferred=ride.transferred,
    )
    db.add(new_ride)
    db.commit()
    db.refresh(new_ride)
    return {"message": "Ride recorded!", "ride_id": new_ride.id}

@app.get("/rides/")
def get_all_rides():
    rides = db.query(SubwayRide).all()
    return rides

@app.get("/rides/{ride_id}")
def get_ride(ride_id: int):
    ride = db.query(SubwayRide).filter(SubwayRide.id == ride_id).first()
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    return ride

@app.delete("/rides/{ride_id}")
def delete_ride(ride_id: int):
    ride = db.query(SubwayRide).filter(SubwayRide.id == ride_id).first()
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    db.delete(ride)
    db.commit()
    return {"message": f"Ride with ID {ride_id} deleted successfully"}

@app.delete("/rides/")
def clear_all_rides(db: Session = Depends(get_db)):
    try:
        db.execute(text("DROP TABLE IF EXISTS rides CASCADE"))
        db.commit()
        return {"message": "Rides table dropped successfully."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error dropping table: {str(e)}")

@app.get("/rides/export")
def export_rides_csv(db: Session = Depends(get_db)):
    rides = db.query(SubwayRide).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "line", "board_stop", "depart_stop", "date", "transferred"])
    for ride in rides:
        writer.writerow([ride.id, ride.line, ride.board_stop, ride.depart_stop, ride.date, ride.transferred])
    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=rides.csv"})


