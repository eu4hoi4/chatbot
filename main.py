import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv
app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"],)
load_dotenv()
API_KEY =  os.getenv("API_KEY")
BASE_URL = "https://api.deepseek.com"
MODEL="deepseek-chat"
class ChatIn(BaseModel):
    message:str
@app.post("/chat")
async def chat(req:ChatIn):
    payload={"model":MODEL,"messages": [{"role": "user", "content": req.message}]}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code != 200:
            return {"reply": f"上游错误 {r.status_code}: {r.text}"}
    print(r.status_code)
    print(r.json())
    answer = r.json()["choices"][0]["message"]["content"]
    return {"reply": answer}
