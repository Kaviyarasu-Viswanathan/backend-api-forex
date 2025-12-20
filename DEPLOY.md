# How to Deploy Backend to Render

## Prerequisites
- A GitHub account.
- A Render.com account.

## Step 1: Push Code to GitHub
You need to push this `backend-server` code to a GitHub repository.

1. **Create a new repository** on GitHub (e.g., `forex-backend`).
2. **Push your code**:
   Open your terminal in `f:\Forex_analysis_app\backend-server` and run:
   ```bash
   git init
   git add .
   git commit -m "Initial commit for Render deployment"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/forex-backend.git
   git push -u origin main
   ```

## Step 2: Deploy on Render
1. **Log in** to [Render.com](https://render.com).
2. Click **New +** -> **Web Service**.
3. **Connect your GitHub repository** (`forex-backend`).
4. Render should automatically detect `python` and use the settings from `render.yaml`.
   - **Name**: `forex-calendar-api` (or custom)
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## Step 3: Configure Environment Variables
In the Render Dashboard for your new service, go to **Environment** from the sidebar and add:

- **Key**: `PERPLEXITY_TOKEN`
- **Value**: (Your token from `.env`)

### **CRITICAL: Add Proxy to Bypass Blocking**
Perplexity blocks Render's cloud IPs. You **MUST** use a residential proxy.

1.  **Get a Proxy**: Use a provider like WebShare, Smartproxy, or Bright Data.
2.  **Add Variable**:
    -   **Key**: `PROXY_URL`
    -   **Value**: `http://user:pass@host:port` (Your actual proxy URL)

## Step 4: Update Frontend
Once deployed, Render will give you a URL (e.g., `https://forex-calendar-api.onrender.com`).

1. Open `f:\Forex_analysis_app\constants\api.ts`.
2. Update `BASE_URL`:
   ```typescript
   BASE_URL: 'https://forex-calendar-api.onrender.com', 
   ```
3. Reload your mobile app.
