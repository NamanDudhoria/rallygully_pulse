from fastapi import FastAPI

app = FastAPI(title="RallyGully Pulse", description="RallyGully Pulse API", version="1.0.0")

@app.get("/")
def read_root():
    return {"message": "Welcome to RallyGully Pulse API"}
