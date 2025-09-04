# Medical Case Generator

A comprehensive medical case generation system that creates detailed cases with diagnostic frameworks and likelihood ratios for emergency medicine education.

## Features

- **AI-Powered Case Generation**: Generate detailed medical cases from brief descriptions
- **Diagnostic Framework Creation**: Multi-tiered diagnostic categories with probability distributions
- **Likelihood Ratios**: Feature-specific likelihood ratios for diagnostic reasoning
- **Three JSON Outputs**: Structured data files for integration with analysis applications
- **Web Interface**: User-friendly Streamlit frontend for case creation and management

## Quick Start

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Set Up Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your actual values:
   # - OpenAI API key
   # - PostgreSQL connection string
   # - Redis URL (optional, defaults to localhost)
   ```

3. **Start Backend**
   ```bash
   python start_backend.py
   ```

4. **Start Frontend** (in a new terminal)
   ```bash
   python start_frontend.py
   ```

5. **Access the Application**
   - Frontend: http://localhost:8501
   - Backend API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

## Architecture

### Backend (FastAPI)
- **API Endpoints**: Case generation, data retrieval, export functionality
- **LLM Integration**: OpenAI GPT-4 for intelligent case generation
- **Database**: PostgreSQL for persistent storage
- **Session Management**: Redis for session handling

### Frontend (Streamlit)
- **Case Creation Interface**: Input forms for case parameters
- **Case Visualization**: Detailed display of generated cases
- **Export Functionality**: Download JSON files for analysis

### Database Schema
- **Cases**: Store case details and metadata
- **Diagnostic Frameworks**: Multi-tier diagnostic structures
- **Feature Likelihood Ratios**: Diagnostic feature relationships

## API Endpoints

- `POST /generate-case`: Generate a new medical case
- `GET /case/{case_id}/output-files`: Export case data as JSON files
- `GET /cases`: List all generated cases

## Output Files

The system generates three JSON files for each case:

1. **case_details.json**: Complete case presentation, patient personality, clinical features
2. **a_priori_probabilities.json**: Probability distributions for diagnostic buckets across tiers
3. **feature_likelihood_ratios.json**: Likelihood ratios organized by clinical category

## Environment Variables

```
OPENAI_API_KEY=your_openai_api_key
POSTGRES_URL=postgresql://user:password@host:port/database
REDIS_URL=redis://localhost:6379/0
BACKEND_URL=http://localhost:8000
```

## Development

The project structure:
```
├── backend/
│   ├── app/          # FastAPI application
│   ├── models/       # Database models and schemas
│   └── utils/        # LLM service and utilities
├── frontend/
│   └── app.py        # Streamlit interface
├── requirements.txt
└── README.md
```

## Integration

This case generator is designed to work with separate analysis applications. The exported JSON files can be consumed by entropy-based diagnostic reasoning systems or other educational analysis tools.

## License

MIT License