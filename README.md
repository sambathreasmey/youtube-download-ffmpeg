# YouTube Media Processing System

This project is a distributed application designed for processing media files (specifically tailored for YouTube-related tasks) using a task-queue architecture. It utilizes Docker Compose to orchestrate microservices, including a web interface, a background worker, a database, a message broker, and an FFmpeg utility container.

---

## Architecture Overview

The system is composed of five distinct services:

* **`web` (Flask):** The main user-facing application that handles incoming requests and queues tasks.
* **`worker` (Python):** A backend process that listens to the message queue and handles long-running tasks.
* **`postgres`:** A PostgreSQL 16 database used for storing application metadata and state.
* **`rabbitmq`:** A message broker that manages the communication between the `web` and `worker` services.
* **`ffmpeg-helper`:** A dedicated utility container containing the FFmpeg binaries, triggered by the `web` and `worker` services to perform media transcoding or manipulation.

---

## Prerequisites

Ensure you have the following installed on your system:
* [Docker](https://docs.docker.com/get-docker/)
* [Docker Compose](https://docs.docker.com/compose/install/)

---

## Project Structure

Ensure your project directory is structured as follows:

```text
.
├── app/
│   ├── app.py          # Flask application code
│   ├── worker.py       # Worker application code
│   └── Dockerfile      # Dockerfile for web and worker
├── downloads/          # Shared volume for processed files
├── docker-compose.yml  # The provided configuration
└── README.md           # This file
```

---

## Getting Started

### 1. Build and Run
To start the entire stack, run the following command in your terminal from the root directory:

```bash
docker-compose up --build
```

### 2. Accessing Services
* **Web Application:** Open your browser and navigate to `http://localhost:5000`.
* **RabbitMQ Management UI:** Open `http://localhost:15672`. 
    * *Username:* `guest`
    * *Password:* `guest`

### 3. Stopping the System
To stop all running containers, use:

```bash
docker-compose down
```

*Note: Adding `-v` to the command above will remove the named volumes, which results in **losing your PostgreSQL database data**.*

---

## Technical Notes

### Docker-outside-of-Docker
The `web` and `worker` containers mount `/var/run/docker.sock`. This allows these containers to send commands to the Docker daemon running on your host machine. Specifically, they use this to perform `docker exec` commands against the `ffmpeg-helper` container to run conversion tasks.

### Health Checks
The system includes built-in health checks for `postgres` and `rabbitmq`. The `web` and `worker` services rely on the `depends_on` condition (specifically `service_healthy`) to ensure that the database and message broker are fully initialized before attempting to connect.

### Environment Configuration
The services are pre-configured to communicate via internal Docker networking:
* **PostgreSQL:** `postgresql+psycopg2://ytuser:ytpass@postgres:5432/ytdb`
* **RabbitMQ:** `amqp://guest:guest@rabbitmq:5672/`

---

## Useful Commands

* **View Logs for a specific service:**
    ```bash
    docker-compose logs -f web
    ```
* **Run a one-time shell command in the worker:**
    ```bash
    docker-compose exec worker /bin/bash
    ```
* **Rebuild and start only one service:**
    ```bash
    docker-compose up --build --no-deps web
    ```
