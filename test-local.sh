#!/bin/bash

# Test script to verify backend starts correctly
echo "Testing backend startup..."

# Start backend in background
python start_backend.py &

# Wait a few seconds for startup
sleep 10

# Check if process is running
if pgrep -f "start_backend.py" > /dev/null; then
    echo "✅ Backend started successfully"
    # Kill the process
    pkill -f "start_backend.py"
    exit 0
else
    echo "❌ Backend failed to start"
    exit 1
fi