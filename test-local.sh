#!/bin/bash

# Local Testing Script
echo "🧪 Testing Medical Case Generator locally..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker Desktop."
    exit 1
fi

# Check for required environment variables
if [ ! -f .env ]; then
    echo "⚠️ Creating .env file template..."
    cat > .env << EOF
OPENAI_API_KEY=your_openai_api_key_here
POSTGRES_URL=your_postgresql_connection_string
REDIS_URL=redis://localhost:6379/0
BACKEND_URL=http://localhost:8000
EOF
    echo "❗ Please edit .env file with your actual values before continuing."
    exit 1
fi

echo "✅ Docker is running"
echo "✅ Environment file exists"

# Build and start services
echo "🔨 Building containers..."
docker-compose build

echo "🚀 Starting services..."
docker-compose up -d

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 30

# Check service health
echo "🏥 Checking service health..."

# Check backend
if curl -f http://localhost:8000/ > /dev/null 2>&1; then
    echo "✅ Backend is healthy"
else
    echo "❌ Backend is not responding"
fi

# Check frontend  
if curl -f http://localhost:8501/_stcore/health > /dev/null 2>&1; then
    echo "✅ Frontend is healthy"
else
    echo "❌ Frontend is not responding"
fi

# Check Redis
if docker exec $(docker-compose ps -q redis) redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis is healthy"
else
    echo "❌ Redis is not responding"
fi

echo ""
echo "🎉 Local testing complete!"
echo "Access your app at:"
echo "  Frontend: http://localhost:8501"
echo "  Backend API: http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo "To stop: docker-compose down"