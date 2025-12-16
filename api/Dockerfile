FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Expose the API port
EXPOSE 8000

# Run the API server
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
