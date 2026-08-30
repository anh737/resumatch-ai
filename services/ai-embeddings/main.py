from fastapi import FastAPI

from router import api_router
from setting import settings

app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=settings.DEBUG)
