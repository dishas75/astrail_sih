"""
Pydantic schemas for the Space Debris Tracking & Collision Risk Engine.
Strictly adheres to OpenAPI 3.1 schema specification.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class TrackPoint(BaseModel):
    t: str = Field(..., description="ISO 8601 UTC timestamp")
    lat: float = Field(..., description="Sub-satellite latitude in degrees")
    lon: float = Field(..., description="Sub-satellite longitude in degrees")
    alt_km: float = Field(..., description="Altitude above Earth surface in kilometers")


class SatelliteOrbitTrack(BaseModel):
    norad_id: int = Field(..., description="NORAD Catalog Number")
    name: str = Field(..., description="Satellite or debris object name")
    inclination_deg: float = Field(..., description="Orbital inclination in degrees")
    apogee_km: float = Field(..., description="Apogee altitude in kilometers")
    perigee_km: float = Field(..., description="Perigee altitude in kilometers")
    track: List[TrackPoint] = Field(default_factory=list, description="Propagated ground-track points")


class OrbitTracksResponse(BaseModel):
    generated_at_utc: str = Field(..., description="Generation time in ISO 8601 UTC")
    propagation_hours: float = Field(..., description="Hours propagated forward")
    step_minutes: float = Field(..., description="Step size in minutes between track points")
    catalog_group: str = Field(..., description="CelesTrak group partition")
    catalog_last_updated_utc: Optional[str] = Field(None, description="Catalog last refresh timestamp")
    satellite_count: int = Field(..., description="Number of satellites returned")
    satellites: List[SatelliteOrbitTrack] = Field(default_factory=list, description="Array of satellite tracks")


class SatelliteRecord(BaseModel):
    norad_id: int = Field(..., description="NORAD Catalog Number")
    name: str = Field(..., description="Object name")
    line1: str = Field(..., description="TLE Line 1")
    line2: str = Field(..., description="TLE Line 2")
    apogee_km: float = Field(..., description="Apogee altitude in km")
    perigee_km: float = Field(..., description="Perigee altitude in km")
    inclination_deg: float = Field(..., description="Inclination in degrees")
    bstar_drag: float = Field(..., description="B* drag term from TLE")


class ConjunctionAlert(BaseModel):
    id: str = Field(..., description="Unique conjunction identifier")
    catalog_group: Optional[str] = Field("active", description="Catalog group partition")
    sat1_id: int = Field(..., description="NORAD ID of first object")
    sat1_name: str = Field(..., description="Name of first object")
    sat2_id: int = Field(..., description="NORAD ID of second object")
    sat2_name: str = Field(..., description="Name of second object")
    tca_utc: str = Field(..., description="Time of Closest Approach (ISO 8601 UTC)")
    miss_distance_km: float = Field(..., description="Miss distance at TCA in kilometers")
    relative_velocity_km_s: float = Field(..., description="Relative velocity at TCA in km/s")
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Normalized collision risk score (0.0 to 1.0)")
    risk_level: str = Field(..., description="Risk tier: CRITICAL, HIGH, MODERATE, LOW")
    sat1_lat_at_tca: Optional[float] = Field(None, description="Object 1 latitude at TCA")
    sat1_lon_at_tca: Optional[float] = Field(None, description="Object 1 longitude at TCA")
    sat1_alt_at_tca_km: Optional[float] = Field(None, description="Object 1 altitude in km at TCA")
    sat2_lat_at_tca: Optional[float] = Field(None, description="Object 2 latitude at TCA")
    sat2_lon_at_tca: Optional[float] = Field(None, description="Object 2 longitude at TCA")
    sat2_alt_at_tca_km: Optional[float] = Field(None, description="Object 2 altitude in km at TCA")


class RecentlyViewedSatellite(BaseModel):
    norad_id: int = Field(..., description="NORAD Catalog Number")
    name: str = Field(..., description="Object name")
    viewed_at: str = Field(..., description="Timestamp of view")
    altitude_km: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    velocity_km_s: Optional[float] = None
    risk_level: Optional[str] = None
    notes: Optional[str] = None


class RecordViewRequest(BaseModel):
    norad_id: int
    name: str
    altitude_km: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    velocity_km_s: Optional[float] = None
    risk_level: Optional[str] = None
    notes: Optional[str] = None


class SavedSatellite(BaseModel):
    norad_id: int
    name: str
    added_at: str
    updated_at: Optional[str] = None
    notes: Optional[str] = ""
    tags: Optional[List[str]] = Field(default_factory=list)
    apogee_km: Optional[float] = None
    perigee_km: Optional[float] = None
    inclination_deg: Optional[float] = None
    altitude_km: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    risk_level: Optional[str] = "NORMAL"


class SaveSatelliteRequest(BaseModel):
    norad_id: int
    name: str
    notes: Optional[str] = ""
    tags: Optional[List[str]] = Field(default_factory=list)
    apogee_km: Optional[float] = None
    perigee_km: Optional[float] = None
    inclination_deg: Optional[float] = None
    altitude_km: Optional[float] = None
    latitude_deg: Optional[float] = None
    longitude_deg: Optional[float] = None
    risk_level: Optional[str] = None

