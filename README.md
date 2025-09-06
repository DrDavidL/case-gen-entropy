# Medical Case Generator

A comprehensive AI-powered medical case generation system that creates detailed emergency medicine cases with diagnostic frameworks and likelihood ratios for medical education and training.

## 🚀 Features

### Core Functionality
- **AI-Powered Case Generation**: Generate detailed medical cases from brief descriptions using OpenAI's latest structured outputs API
- **Multi-Tier Diagnostic Framework**: Create 3-tier diagnostic categories (broad → specific → precise) with probability distributions
- **Evidence-Based Likelihood Ratios**: Generate clinically meaningful likelihood ratios for diagnostic reasoning
- **Interactive Case Editing**: Preview, edit, and refine generated cases before saving
- **Multiple Export Formats**: Export cases as JSON, CSV, or Excel files for analysis applications

### Advanced Features
- **Session Management**: Redis-powered session handling for case editing workflows
- **Database Persistence**: PostgreSQL storage with comprehensive case metadata
- **Authentication System**: Secure access control for case preview and editing
- **Modern API Architecture**: FastAPI backend with comprehensive OpenAPI documentation
- **Responsive Web Interface**: Streamlit-based frontend with intuitive case management
- **Azure Cloud Deployment**: Production-ready deployment on Azure Container Apps

### Export Capabilities
- **Three JSON Outputs**: Structured data files optimized for integration with analysis applications
- **CSV Export**: Tabular data format for spreadsheet analysis
- **Excel Export**: Multi-sheet workbooks with formatted case data
- **Feature-LR Matrix**: Pre-computed matrices for entropy-based diagnostic reasoning

## 📋 Quick Start

### Local Development

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set Up Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your actual values:
   # - OpenAI API key (required)
   # - PostgreSQL connection string (required)
   # - Redis URL (optional, defaults to localhost)
   ```

3. **Start Services**
   ```bash
   # Start backend (Terminal 1)
   python start_backend.py
   
   # Start frontend (Terminal 2)
   python start_frontend.py
   ```

4. **Access the Application**
   - Frontend: http://localhost:8501
   - Backend API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

### Docker Development

```bash
# Start all services with Docker Compose
docker-compose up

# Access:
# - Frontend: http://localhost:8501
# - Backend: http://localhost:8000
# - Redis: localhost:6379
```

### Production Deployment (Azure)

The system is production-ready and deployed on Azure Container Apps:

```bash
# Deploy to Azure Container Apps
./deploy-container-apps.sh

# Services will be available at:
# - Frontend: https://frontend-app.[environment].azurecontainerapps.io
# - Backend: https://backend-app.[environment].azurecontainerapps.io
```

## 🏗️ Architecture

### Technology Stack
- **Backend**: FastAPI with Pydantic v2 for data validation
- **Frontend**: Streamlit for interactive web interface
- **AI/ML**: OpenAI GPT-4o with structured outputs (latest API)
- **Database**: PostgreSQL for persistent storage
- **Cache/Sessions**: Redis for session management
- **Deployment**: Azure Container Apps with automatic scaling
- **Container Registry**: Azure Container Registry for image management

### System Architecture
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Streamlit     │────│   FastAPI       │────│   PostgreSQL    │
│   Frontend      │    │   Backend       │    │   Database      │
│                 │    │                 │    │                 │
│ • Case Creation │    │ • LLM Service   │    │ • Case Storage  │
│ • Case Editing  │    │ • API Endpoints │    │ • User Data     │
│ • Export Tools  │    │ • Authentication│    │ • Metadata      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │              ┌─────────────────┐             │
         │              │     Redis       │             │
         │              │   Session Store │             │
         │              │                 │             │
         │              │ • Case Sessions │             │
         │              │ • Edit State    │             │
         └──────────────│ • Cache Layer   │─────────────┘
                        └─────────────────┘
                                 │
                        ┌─────────────────┐
                        │   OpenAI API    │
                        │                 │
                        │ • GPT-4o Model  │
                        │ • Structured    │
                        │   Outputs       │
                        └─────────────────┘
```

### Backend Components
- **LLM Service**: Modern OpenAI integration with structured outputs
- **Database Models**: Comprehensive schema for cases, frameworks, and likelihood ratios
- **Authentication**: Secure credential verification system
- **Export Utilities**: Multi-format export with simulator compatibility
- **Session Management**: Redis-based temporary storage for editing workflows

### Frontend Features
- **Case Creation Wizard**: Step-by-step case generation interface
- **Interactive Preview**: Real-time case editing and refinement
- **Export Dashboard**: Multiple format downloads with preview
- **Case Library**: Browse and manage previously generated cases
- **Authentication Interface**: Secure login for protected features

## 📊 Generated Data Structure

### Case Details JSON
```json
{
  "presentation": "Detailed patient presentation...",
  "patient_personality": "Communication style description...",
  "history_questions": [
    {
      "question": "Clinical question...",
      "expected_answer": "Patient response..."
    }
  ],
  "physical_exam_findings": [
    {
      "examination": "Exam component...",
      "findings": "Clinical findings..."
    }
  ],
  "diagnostic_workup": [
    {
      "test": "Diagnostic test...",
      "rationale": "Clinical rationale..."
    }
  ]
}
```

### Diagnostic Framework JSON
```json
{
  "tiers": [
    {
      "tier_level": 1,
      "buckets": [
        {
          "name": "Cardiovascular",
          "description": "Heart and vascular conditions..."
        }
      ],
      "a_priori_probabilities": {
        "Cardiovascular": 0.35,
        "Respiratory": 0.25
      }
    }
  ]
}
```

### Feature Likelihood Ratios JSON
```json
{
  "feature_likelihood_ratios": [
    {
      "feature_name": "Chest pain",
      "feature_category": "history",
      "diagnostic_bucket": "Ischemic Heart Disease",
      "tier_level": 2,
      "likelihood_ratio": 3.5
    }
  ]
}
```

## 🔌 API Endpoints

### Core Endpoints
- `GET /` - API health check
- `POST /generate-case` - Generate complete medical case
- `POST /preview-case` - Generate case for preview/editing (requires auth)
- `PUT /edit-case` - Update case during editing session
- `POST /save-case` - Save edited case to database

### Data Retrieval
- `GET /cases` - List all generated cases
- `GET /case/{case_id}` - Get specific case details
- `GET /case/{case_id}/output-files` - Export case as JSON/CSV/Excel

### Export Options
- **JSON**: Three structured files (case_details, diagnostic_framework, feature_likelihood_ratios)
- **CSV**: Tabular format for spreadsheet analysis
- **Excel**: Multi-sheet workbook with formatted data

## 🔧 Configuration

### Environment Variables
```bash
# Required
OPENAI_API_KEY=your_openai_api_key_here
POSTGRES_URL=postgresql://user:pass@host:port/db

# Optional (with defaults)
REDIS_URL=redis://localhost:6379/0
BACKEND_URL=http://localhost:8000

# Authentication (for preview/edit features)
APP_USERNAME=your_username
APP_PASSWORD=your_password

# Azure Deployment
ACR_USERNAME=your_acr_username
ACR_PASSWORD=your_acr_password
```

### OpenAI API Requirements
- **Model**: GPT-4o (2024-08-06) or later
- **Features**: Structured outputs with JSON schema
- **API Version**: v1.106.1+

## 🚀 Recent Improvements

### Version 2.0 Features
- **Modern OpenAI API**: Updated to latest structured outputs with `response_format`
- **Pydantic v2**: Full migration to Pydantic v2 with proper model validation
- **Azure Container Apps**: Production deployment with automatic scaling
- **Enhanced Error Handling**: Comprehensive error management and retry logic
- **Session-Based Editing**: Redis-powered case editing workflows
- **Multi-Format Export**: JSON, CSV, and Excel export capabilities

### Performance Optimizations
- **Parallel Processing**: Concurrent generation of case components
- **Caching Layer**: Redis-based caching for improved response times
- **Connection Pooling**: Optimized database connection management
- **Load Balancing**: Azure Container Apps automatic scaling

### Security Enhancements
- **Credential Validation**: Secure authentication for protected endpoints
- **Environment Variable Management**: Secure secret handling in Azure
- **HTTPS Enforcement**: SSL/TLS encryption for all communications
- **Input Sanitization**: Comprehensive input validation and sanitization

## 🏭 Development Workflow

### Project Structure
```
medical-case-generator/
├── backend/
│   ├── app/
│   │   └── main.py              # FastAPI application
│   ├── models/
│   │   ├── database.py          # Database models
│   │   ├── schemas.py           # API schemas
│   │   ├── structured_outputs.py # OpenAI response models
│   │   └── editing_schemas.py   # Session management schemas
│   └── utils/
│       ├── llm_service.py       # OpenAI integration
│       ├── simulator_export.py  # Export utilities
│       └── auth.py              # Authentication
├── frontend/
│   └── app.py                   # Streamlit interface
├── docker-compose.yml           # Local development
├── Dockerfile.backend           # Backend container
├── Dockerfile.frontend          # Frontend container
├── deploy-container-apps.sh     # Azure deployment
├── requirements.txt             # Python dependencies
└── README.md
```

### Development Guidelines
1. **Code Quality**: All code follows PEP 8 standards with type hints
2. **Testing**: Comprehensive testing of API endpoints and LLM integration
3. **Documentation**: OpenAPI/Swagger documentation for all endpoints
4. **Error Handling**: Robust error handling with meaningful error messages
5. **Logging**: Structured logging for debugging and monitoring

## 🔄 Integration & Usage

### For Medical Education
- Generate diverse clinical cases for student training
- Create standardized scenarios for assessment
- Develop case-based learning materials
- Support simulation training programs

### For Diagnostic Research
- Export structured data for entropy analysis
- Generate likelihood ratio matrices
- Support Bayesian diagnostic reasoning research
- Integrate with existing analysis pipelines

### For Clinical Decision Support
- Create reference cases for diagnostic training
- Generate probability distributions for clinical scenarios
- Support evidence-based medicine initiatives
- Enhance diagnostic reasoning education

## 📈 Performance Metrics

### Generation Times
- **Case Details**: ~8-12 seconds
- **Diagnostic Framework**: ~6-8 seconds  
- **Likelihood Ratios**: ~8-10 seconds
- **Total Case Generation**: ~22-30 seconds

### Scalability
- **Azure Container Apps**: Auto-scaling 1-5 replicas
- **Database**: PostgreSQL with connection pooling
- **Cache**: Redis with session persistence
- **API Rate Limits**: Configurable per deployment

## 📝 License

MIT License - see LICENSE file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Update documentation
5. Submit a pull request

## 📞 Support

For issues, questions, or contributions:
- **Issues**: GitHub Issues tracker
- **Documentation**: API docs at `/docs` endpoint
- **Development**: See development workflow above

---

**Built with modern AI and cloud technologies for scalable medical education.**