from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.routes import accounts

app = FastAPI()

app.include_router(accounts.router)

# Rest of the file remains the same