# FastAPI Backend with Supabase PostgreSQL

This FastAPI backend replicates your Supabase table structure using SQLAlchemy models and connects directly to your Supabase PostgreSQL database.

## Project Structure

```
├── main.py                 # FastAPI application entry point
├── models.py              # SQLAlchemy models (replicate Supabase tables)
├── schemas.py             # Pydantic schemas for request/response validation
├── database.py            # Database configuration and connection
├── routes/
│   ├── groups.py          # Group-related endpoints
│   └── contributions.py   # Contribution-related endpoints
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variables example
└── README.md             # This file
```

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Environment Configuration

1. Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

2. Update the `.env` file with your Supabase credentials:
```env
DATABASE_URL=postgresql://postgres:[YOUR-PASSWORD]@db.[YOUR-PROJECT-REF].supabase.co:5432/postgres
```

You can find your database URL in your Supabase dashboard under:
- Settings → Database → Connection string → URI

### 3. Database Setup

The application will automatically create tables when you first run it. However, if you want to use database migrations, you can set up Alembic:

```bash
# Initialize Alembic (optional)
alembic init alembic

# Generate migration
alembic revision --autogenerate -m "Initial migration"

# Apply migration
alembic upgrade head
```

### 4. Run the Application

```bash
# Development mode
python main.py

# Or with uvicorn directly
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at:
- **API**: http://localhost:8000
- **Interactive Docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## API Endpoints

### Groups API (`/api/v1/groups`)

- `POST /api/v1/groups` - Create a new group
- `GET /api/v1/groups` - Get all groups (with filtering, pagination, search)
- `GET /api/v1/groups/{group_id}` - Get group details
- `PUT /api/v1/groups/{group_id}` - Update group
- `DELETE /api/v1/groups/{group_id}` - Delete group

#### Group Members
- `POST /api/v1/groups/{group_id}/members` - Add member to group
- `GET /api/v1/groups/{group_id}/members` - Get group members
- `PUT /api/v1/groups/{group_id}/members/{member_id}` - Update member status
- `DELETE /api/v1/groups/{group_id}/members/{member_id}` - Remove member

#### Group Admins
- `POST /api/v1/groups/{group_id}/admins` - Add admin to group
- `GET /api/v1/groups/{group_id}/admins` - Get group admins
- `DELETE /api/v1/groups/{group_id}/admins/{admin_id}` - Remove admin

#### User Groups
- `GET /api/v1/groups/user/{user_id}` - Get user's groups

### Contributions API (`/api/v1/contributions`)

- `POST /api/v1/contributions` - Create contribution
- `GET /api/v1/contributions` - Get all contributions (with filtering)
- `GET /api/v1/contributions/{contribution_id}` - Get contribution details
- `PUT /api/v1/contributions/{contribution_id}` - Update contribution
- `DELETE /api/v1/contributions/{contribution_id}` - Delete contribution
- `POST /api/v1/contributions/{contribution_id}/pay` - Mark contribution as paid

#### Group Contributions
- `GET /api/v1/contributions/group/{group_id}` - Get group contributions
- `GET /api/v1/contributions/group/{group_id}/summary` - Get group contribution summary

#### User Contributions
- `GET /api/v1/contributions/user/{user_id}` - Get user contributions
- `GET /api/v1/contributions/user/{user_id}/overdue` - Get user overdue contributions

## Features

### 1. Complete CRUD Operations
- Create, Read, Update, Delete for all entities
- Proper error handling and validation
- Database constraints and relationships

### 2. Advanced Filtering and Pagination
- Search functionality
- Status filtering
- Date range filtering
- Sorting options
- Pagination support

### 3. Business Logic
- Automatic group member/admin management
- Contribution status tracking
- Payment processing
- Overdue contribution detection

### 4. Data Validation
- Pydantic schemas for request/response validation
- Type safety with SQLAlchemy models
- UUID handling for all IDs

### 5. Database Relationships
- Foreign key constraints
- One-to-many relationships
- Many-to-many relationships through junction tables

## Models Overview

### Core Models
- **Profile**: User profile information
- **Group**: Savings/investment groups
- **GroupMember**: Group membership tracking
- **GroupAdmin**: Group administration roles
- **Contribution**: Member contributions to groups
- **Notification**: User notifications
- **AvalancheToken**: Cryptocurrency token information

### Enums
- **ContributionStatus**: pending, completed, overdue
- **GroupStatus**: active, inactive, completed
- **MemberStatus**: active, inactive, pending
- **NotificationType**: contribution_due, payment_received, group_update, admin_message

## Database Connection

The application connects directly to your Supabase PostgreSQL database using SQLAlchemy. This provides:
- Full ORM capabilities
- Connection pooling
- Transaction management
- Type safety
- Migration support

## Next Steps

1. **Add Authentication**: Implement JWT or session-based authentication
2. **Add More Routes**: Create routes for profiles, notifications, etc.
3. **Add Background Tasks**: Implement scheduled tasks for contribution reminders
4. **Add Testing**: Create unit and integration tests
5. **Add Logging**: Implement comprehensive logging
6. **Add Caching**: Implement Redis caching for frequently accessed data

## Example Usage

### Create a Group
```bash
curl -X POST "http://localhost:8000/api/v1/groups" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Monthly Savings",
    "description": "Monthly savings group",
    "contribution_amount": 1000,
    "contribution_frequency": "monthly",
    "max_members": 10,
    "start_date": "2024-01-01T00:00:00",
    "created_by": "123e4567-e89b-12d3-a456-426614174000"
  }'
```

### Get Groups with Filtering
```bash
curl "http://localhost:8000/api/v1/groups?status=active&search=savings&limit=10&skip=0"
```

This setup provides a robust, scalable FastAPI backend that directly integrates with your Supabase PostgreSQL database while maintaining all the benefits of SQLAlchemy's ORM capabilities.