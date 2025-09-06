
@app.get("/test-env")
async def test_env():
    """Test environment variables"""
    return {
        "openai_key_present": bool(os.getenv("OPENAI_API_KEY")),
        "postgres_url_present": bool(os.getenv("POSTGRES_URL")), 
        "redis_url": os.getenv("REDIS_URL", "not_set")
    }

@app.get("/test-db")
async def test_database():
    """Test database connectivity"""
    try:
        db = next(get_db())
        result = db.execute("SELECT 1 as test")
        return {"status": "database_connected", "result": result.fetchone()}
    except Exception as e:
        return {"status": "database_error", "error": str(e)}
