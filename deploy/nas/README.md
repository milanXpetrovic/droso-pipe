# Deploying the app on the NAS

The app runs as three containers: `db` (Postgres), `app` (Django + gunicorn), `backup` (daily `pg_dump`).
The image is built by GitHub Actions; the NAS only pulls it.

## 1. Build the image (on any machine with the repo)

    git tag app-v0.1.0
    git push origin app-v0.1.0

GitHub → Actions shows the build. The first time, open GitHub → your profile → Packages →
`droso-pipe-app` → Package settings → Change visibility → **Public** (the image holds no secrets),
so the NAS can pull it without a login.

## 2. Folders on the NAS (System → Shell, `sudo -i`)

    mkdir -p /mnt/lab/apps/postgres /mnt/lab/apps/media /mnt/lab/apps/backups
    chown 3002:3003 /mnt/lab/apps/media          # pipeline
    chmod 700 /mnt/lab/apps/postgres /mnt/lab/apps/backups

## 3. Install

Apps → Discover → ⋮ → **Install via YAML**, name `droso`, paste `docker-compose.yml` with every
`CHANGE_ME` replaced. Generate the secret key and DB password with:

    python3 -c "import secrets; print(secrets.token_urlsafe(50))"

Keep them in a password manager. Never commit them.

## 4. First start only (System → Shell)

    sudo docker ps --format '{{.Names}}' | grep app          # find the container name
    sudo docker exec -it <app-container> python manage.py loaddata catalogs lab
    sudo docker exec -it <app-container> python manage.py setup_roles
    sudo docker exec -it <app-container> python manage.py createsuperuser

Open http://10.11.48.18:8080/admin

## 5. People

Admin → Users → Add: give each person their own account, tick **Staff status**, add them to the
**Operators** group. Operators enter recordings; only superusers edit catalogues.

## Updating

Tag a new version (`app-v0.1.1`), wait for the build, then in TrueNAS edit the app and change the
image tag. Migrations run automatically on start. Roll back by setting the previous tag.

## Restore test (do this once)

    sudo docker exec -i <db-container> createdb -U droso droso_restore_test
    sudo docker exec -i <db-container> pg_restore -U droso -d droso_restore_test < /mnt/lab/apps/backups/droso-YYYY-MM-DD.dump
