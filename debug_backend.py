#!/usr/bin/env python3

import logging
import sys
import os

# Configure logging to see what's happening during startup
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Starting debug backend startup...")

try:
    logger.info("Importing uvicorn...")
    import uvicorn
    logger.info("Uvicorn imported successfully")
except Exception as e:
    logger.error(f"Failed to import uvicorn: {e}")
    sys.exit(1)

try:
    logger.info("Importing backend.app.main...")
    from backend.app.main import app
    logger.info("Backend app imported successfully")
except Exception as e:
    logger.error(f"Failed to import backend app: {e}")
    import traceback
    logger.error(traceback.format_exc())
    sys.exit(1)

logger.info("Starting uvicorn server...")
try:
    uvicorn.run(
        "backend.app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
        log_level="debug"
    )
except Exception as e:
    logger.error(f"Failed to start uvicorn server: {e}")
    import traceback
    logger.error(traceback.format_exc())
    sys.exit(1)