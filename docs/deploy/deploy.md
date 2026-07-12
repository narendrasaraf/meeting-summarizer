# EC2 Deployment Runbook

This guide documents the step-by-step procedure to manually deploy the **Meeting Summarizer** application on a single AWS EC2 instance running Docker and Docker Compose. This architecture keeps the deployment simple, fully reviewable, and matches the local containerized environment.

---

## 1. Instance Provisioning & Security Group

### VM Sizing
- **AMI**: Ubuntu Server 24.04 LTS (HVM), SSD Volume Type.
- **Instance Type**: `t3.micro` (free tier) or `t3.small` (recommended for faster builds). The application is lightweight and offloads heavy ASR/LLM compute to API providers, so minimal local CPU/RAM is required.
- **Storage**: 20 GB gp3 EBS volume (provides enough space for Docker images, cache, and active uploaded audio clips).

### Security Group Configuration
Create a security group with the following inbound rules:

| Protocol | Port Range | Source | Description |
|---|---|---|---|
| **TCP** | `22` | `My IP` (Recommended) | Secure SSH access to the host interface. |
| **TCP** | `80` | `0.0.0.0/0`, `::/0` | Public HTTP access to the React frontend. |

> [!IMPORTANT]
> **Do NOT expose port 8000 (Backend API) publicly.** The backend API container maps to the host's loopback interface only (`127.0.0.1:8000`), keeping it internal behind the Nginx reverse proxy running inside the frontend container.

---

## 2. Server Environment Setup

SSH into your newly provisioned EC2 instance:
```bash
ssh -i /path/to/your-key.pem ubuntu@your-ec2-public-dns.compute-1.amazonaws.com
```

### Install Docker Engine & Compose
Run the following commands to install the latest Docker and Docker Compose packages:

```bash
# Update package database
sudo apt-get update -y

# Install dependencies
sudo apt-get install -y ca-certificates curl gnupg

# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Set up the repository
echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker components
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add ubuntu user to docker group to run docker commands without sudo
sudo usermod -aG docker ubuntu
```

*Note: Log out and log back in to apply the group membership changes.*

---

## 3. Application Deployment

### Clone the Repository
Clone the application to the default user directory:

```bash
cd /home/ubuntu
git clone https://github.com/your-username/meeting-summarizer.git
cd meeting-summarizer
```

### Configure Environment Variables
Create the production environment file from the template:

```bash
cp backend/.env.example backend/.env
nano backend/.env
```

Ensure the following production config is set:
```ini
# Force free-tier providers to protect against OpenAI budget runaways
DEMO_MODE=true

# Select active free-tier provider
PROVIDER=groq
GROQ_API_KEY=gsk_... # Insert your live Groq API Key

# Or use Gemini
# PROVIDER=gemini
# GEMINI_API_KEY=AIzaSy...

# Set rate limiting to protect public upload endpoint from abuse
UPLOAD_RATE_LIMIT=3/hour

# Database location inside container volume
DATABASE_URL=sqlite:///./meetings.db
```

### Launch the Application
Build and start the container services. Override the default `FRONTEND_PORT` to `80` so Nginx listens on the public HTTP interface:

```bash
# Launch with frontend listening on port 80
FRONTEND_PORT=80 docker compose up -d --build
```

Verify that both containers are running successfully:
```bash
docker compose ps
```

---

## 4. Disk Hygiene & Maintenance (Cron)

Since this is a public demo without user authentication, curious users or automated crawlers may upload audio files. To prevent the EC2 instance from running out of disk space, set up an hourly cron job to automatically delete raw uploaded audio clips older than 60 minutes.

Open the crontab editor:
```bash
crontab -e
```

Add the following line to the bottom of the file:
```cron
# Every hour, delete files in backend/uploads older than 60 minutes, preserving .gitkeep
0 * * * * find /home/ubuntu/meeting-summarizer/backend/uploads -type f -not -name ".gitkeep" -mmin +60 -delete
```

This ensures the disk usage remains small and stable indefinitely.
