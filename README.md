# AstroPulse

AstroPulse is a hackathon prototype for an astronaut wellness dashboard. Members of a cohort register with a private invite code. Each account has its own health logs and mission tasks stored in MongoDB; members cannot choose or view another member's profile.

## Run locally

1. Install Python 3.10 or later and MongoDB Community Server, or create a MongoDB Atlas cluster.
2. In PowerShell, from this folder, create your local configuration:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Edit `.env`:
   - Set `MONGODB_URI` to your MongoDB Atlas connection string, or keep the local MongoDB address.
   - Set `BATCH_NAME` and a private `BATCH_INVITE_CODE`. Share the invite code only with your batch.
   - Replace `SECRET_KEY` with a long random value. Do not commit `.env` or share your MongoDB password.
   - For Atlas, add your development IP address to the cluster's network access list and create a database user.
4. Install dependencies and start the app:

   ```powershell
   python -m pip install -r requirements.txt
   python app.py
   ```

5. Open [http://127.0.0.1:5001](http://127.0.0.1:5001), choose **Create an account**, and enter the invite code.

Set `COOKIE_SECURE=true` when serving the site over HTTPS. Keep it `false` for local HTTP development.

## Data and privacy

MongoDB stores users, password hashes, health logs, and mission tasks. Passwords are hashed before storage. Each health log and task is queried by the signed-in user's MongoDB ID. The dashboard does not expose a member list or a user-selectable profile.

The old SQLite/demo records are not imported. New accounts start with an empty health history and their own starter mission tasks. Health alerts and hazard scores are illustrative hackathon logic, not clinical advice, NASA thresholds, or a medical device.

## Database collections

- `users`: name, email, password hash, batch name, and mission phase
- `health_logs`: personal vitals, timestamp, and optional note
- `mission_tasks`: personal routine checklist and reward points

## Research resources

The signed-in dashboard includes a NASA and CSA resource library with short summaries and links to the original material:

- [NASA Human Research Program: Five Hazards of Human Spaceflight](https://www.nasa.gov/hrp/hazards/)
- [NASA Human Research Program: Risks of Human Spaceflight](https://www.nasa.gov/hrp/risks/)
- [NASA Human Research Program: Ongoing Research in Space](https://www.nasa.gov/hrp/ongoing-hrp-research/)
- [Canadian Space Agency: Monitoring Radiation for Astronaut Health](https://www.asc-csa.gc.ca/eng/news/articles/2026/2026-02-26-monitoring-radiation-astronaut-health.asp)
- [Canadian Space Agency: Why Conduct Science Experiments in Space?](https://www.asc-csa.gc.ca/eng/iss/science/why-do-we-conduct-science-experiments-in-space.asp)

Dashboard hazard percentages are illustrative prototype indicators, not NASA measurements or official risk thresholds. Use the linked agency resources for authoritative background.
