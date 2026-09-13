/* Template only - real values come from environment variables at build time
 * (see build-config.js). This file has no secrets and is safe to commit.
 * Cloudflare Pages: set API_BASE_URL + ACCESS_KEY in the dashboard.
 * Local dev: values are read from ../.env automatically. */
window.APP_CONFIG = {
  API_BASE_URL: "https://your-backend.onrender.com",
  ACCESS_KEY: "paste-the-same-access-key-as-the-backend-here"
};
