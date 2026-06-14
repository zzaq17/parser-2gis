# Debian 13 deployment

Stage 1 runs natively under systemd. PostgreSQL is installed on the same VDS,
while Chromium is managed by the pinned Playwright release rather than by
Debian's `chromium` package.

## Initial installation

```bash
sudo sh deploy/install-debian13.sh
sudo install -m 0640 -o root -g stage1-2gis \
  deploy/stage1-2gis.env.example /etc/stage1-2gis/stage1-2gis.env
```

Create a release:

```bash
release_id="$(date +%Y%m%d%H%M%S)"
sudo install -d -o stage1-2gis -g stage1-2gis "/opt/stage1-2gis/releases/$release_id"
sudo cp -a . "/opt/stage1-2gis/releases/$release_id/source"
sudo -u stage1-2gis python3 -m venv "/opt/stage1-2gis/releases/$release_id/source/.venv"
sudo -u stage1-2gis "/opt/stage1-2gis/releases/$release_id/source/.venv/bin/pip" install .
sudo env PLAYWRIGHT_BROWSERS_PATH=/opt/stage1-2gis/browsers/1.60.0 \
  "/opt/stage1-2gis/releases/$release_id/source/.venv/bin/python" -m playwright install-deps chromium
sudo -u stage1-2gis env PLAYWRIGHT_BROWSERS_PATH=/opt/stage1-2gis/browsers/1.60.0 \
  "/opt/stage1-2gis/releases/$release_id/source/.venv/bin/python" -m playwright install chromium
sudo ln -sfn "/opt/stage1-2gis/releases/$release_id/source" /opt/stage1-2gis/current
```

If outbound traffic requires a proxy, both PyPI and `cdn.playwright.dev` must be
reachable. When the proxy cannot resolve the Playwright CDN, configure an
artifact mirror through `PLAYWRIGHT_DOWNLOAD_HOST`; do not fall back to an
uncontrolled system Chromium version.

Apply the schema and start the worker:

```bash
sudo -u stage1-2gis env \
  "$(cat /etc/stage1-2gis/stage1-2gis.env | xargs)" \
  /opt/stage1-2gis/current/.venv/bin/python -m stage1_2gis migrate
sudo systemctl enable --now stage1-2gis-worker
```

Do not use the `env "$(cat ...)"` form in automation when values can contain
spaces. For scripted deployment, source a root-readable shell environment file
or use systemd's `EnvironmentFile`.

## Release update

Build each release in a new directory and browser cache. Before switching
`/opt/stage1-2gis/current`, run:

```bash
python -m pytest -m "not live"
python -m stage1_2gis smoke \
  --url "https://2gis.ru/moscow/search/аптеки" \
  --max-records 5
```

Keep the previous release and browser directory. Rollback consists of switching
the `current` symlink back and restarting `stage1-2gis-worker`.
