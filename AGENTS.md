
# Agents Guide

This document describes how to interact with and use this project effectively.

## Project Overview

**Incremental Serv** is the backend API for [Incremental](https://github.com/inrenping/incremental.icu), a fitness tracking platform.

### Key Features

- Multi-platform fitness data integration (Garmin, Coros, Google)
- User authentication and social login
- Activity tracking and heart rate monitoring
- Task management and async processing
- OSS file storage support (Aliyun OSS, AWS S3)

### Tech Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL (SQLAlchemy ORM)
- **Task Queue**: Async task processing
- **Auth**: JWT + OAuth2
- **Storage**: Aliyun OSS / AWS S3
- **Email**: Resend

## Project Structure

```
blunt-serv/
├── app/
│   ├── api/v1/endpoints/      # API route handlers
│   ├── core/                  # Core configurations &amp; security
│   ├── db/                    # Database session management
│   ├── models/                # SQLAlchemy models
│   ├── services/              # Business logic layer
│   ├── utils/                 # Utility functions
│   └── main.py                # FastAPI entry point
├── tests/                     # Test files
├── requirements.txt           # Python dependencies
└── readme.rst                 # Project documentation
```

## Getting Started

### Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment Setup

Copy `.env.example` to `.env` and configure:

- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - JWT signing key
- OAuth credentials (Google, GitHub)
- OSS configuration
- Resend API key

### Running

```bash
python -m uvicorn app.main:app --reload
```

## Code Conventions

### Code Generation

This project primarily uses **Google Gemini Assist** for code generation.

### PR Review

Pull requests trigger AI review when commented with: `/gemini-review`

## Key Modules

### API Endpoints

- `/api/v1/auth` - Authentication
- `/api/v1/user` - User management
- `/api/v1/garmin` - Garmin integration
- `/api/v1/coros` - Coros integration
- `/api/v1/google` - Google integration
- `/api/v1/task` - Task management

### Services

Located in [app/services/](file:///home/inrenping/projects/github/blunt-serv/app/services)

## Development Workflow

1. Keep code consistent with existing patterns
2. Follow FastAPI best practices
3. Use Pydantic models for request/response validation
4. Add tests for new features when possible
