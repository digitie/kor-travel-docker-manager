from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tripmate_manager.api.routes import router as container_router

app = FastAPI(
    title="TripMate Manager API",
    description="API for monitoring and managing TripMate PostgreSQL and RustFS services.",
    version="0.1.0"
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(container_router, prefix="/api", tags=["containers"])

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "tripmate-manager-backend"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
