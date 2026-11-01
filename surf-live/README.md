# Kauai Connect surf cards

Rebuilds the surf cards every hour and publishes them with GitHub Pages.

## One-time setup (about 10 minutes)
1. Sign in at github.com (free account is fine) and click **New repository**. Name it `surf-cards`, set it to **Public**, and create it.
2. On the new repo page click **uploading an existing file** and drag in EVERYTHING from this folder, including the hidden `.github` folder (if Windows hides it, turn on View > Show > Hidden items). The files must keep their folders: `scripts/build_surf.py`, `scripts/surf_template/surf_cards.html`, `surf_spot.csv`, `.github/workflows/surf.yml`. Commit to `main`.
3. Open **Settings > Pages**. Under **Build and deployment > Source** choose **GitHub Actions**.
4. Open the **Actions** tab, click **Refresh surf cards**, then **Run workflow**. It takes about two minutes.
5. When it turns green, your page is at `https://<your-github-name>.github.io/surf-cards/`. Open it to check.
6. Put it on /surf-reports-all/ in a Custom HTML block:
   `<iframe src="https://<your-github-name>.github.io/surf-cards/" style="width:100%;height:1800px;border:0" loading="lazy" title="Kauai surf reports"></iframe>`

## If a run turns red
Open the run and read the "Stop if Surfline gave us nothing" step. If it says 0 spots have a rating, Surfline is refusing GitHub's servers. The last good page stays online. Tell Claude and keep running `build_surf.py` on your PC instead.

## Changing the spots
Edit `surf_spot.csv` in the repo (pencil icon); the page rebuilds on its own.
