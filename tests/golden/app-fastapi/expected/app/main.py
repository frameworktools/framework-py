from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Demo App")


class Message(BaseModel):
    message: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/echo")
def echo(body: Message) -> Message:
    return body
