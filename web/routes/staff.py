"""staff.py — Staff-only pages (roster, approvals, admin)."""
from fastapi import APIRouter

router = APIRouter(prefix="/staff", tags=["staff"])

# Routes added in Chunk 5 (staff roster, review queue, criteria admin)
