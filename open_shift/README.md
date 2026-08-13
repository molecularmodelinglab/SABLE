# Deploying SABLE to Red Hat OpenShift

This guide walks through everything you need to run SABLE (API, background workflow runner, and optional frontend) on an OpenShift cluster. It assumes you are moving the services defined in `docker-compose.yml` (PostgreSQL, Redis, API, migrations, CLI utilities, and UI) into Kubernetes-first resources.

---

## 1. Prerequisites

- Access to an OpenShift 4.x cluster and the `oc` CLI (logged in via `oc login ...`).
- A project/namespace (replace `<project>` in commands with yours).
- Container registry for images (Red Hat Quay, Docker Hub, ECR, etc.) with login configured.
- DNS/SSL strategy for exposing the API and UI (OpenShift Routes support TLS termination).
- OpenShift storage class that supports ReadWriteOnce PVCs for PostgreSQL and Redis.
- Optional: OpenShift Operators for PostgreSQL and Redis if you prefer managed services.

---

## 2. Prepare the container image

1. **Build and push the image** (or let OpenShift build it):
   ```bash
   # Option A: Build locally and push
   podman build -t quay.io/<org>/sable-api:latest .
   podman push quay.io/<org>/sable-api:latest

   # Option B: Use OpenShift Binary Build
   oc new-build --name=sable-api --binary --strategy=docker
   
   oc patch bc/sable-api --type=merge -p '{"spec":{"strategy":{"dockerStrategy":{"dockerfilePath":"Dockerfile.prod"}}}}'

   oc start-build sable-api --from-dir=. --follow
   ```

2. **Frontend image** (`ui/Dockerfile`) can be built separately if you plan to host the static UI via Nginx. Otherwise, run Vite locally.

---

## 3. Create the OpenShift project and import the images

```bash
oc new-project < project >

# Import pre-built images (skip if using OpenShift BuildConfig)
oc import-image sable-api:latest \
  --from=quay.io/<org>/sable-api:latest --confirm
oc import-image sable-frontend:latest \
  --from=quay.io/<org>/sable-frontend:latest --confirm
```

---

## 4. Manage secrets and configuration

Split sensitive and non-sensitive values into separate resources.

1. **Secrets** (`SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, OAuth keys, LLM keys):
   ```bash
   oc create secret generic sable-secrets \
     --from-literal=SECRET_KEY="change_this" \
     --from-literal=DATABASE_URL="postgresql://sable_user:***@postgresql:5432/sable" \
     --from-literal=REDIS_URL="redis://:***@redis:6379/0" \
     --from-literal=OPENAI_API_KEY="..." \
     --from-literal=GOOGLE_API_KEY="..."
   ```

2. **ConfigMap** (non-secret configuration—`ENVIRONMENT`, `LLM_PROVIDER`, etc.):
   ```bash
   oc create configmap sable-config \
     --from-literal=ENVIRONMENT=production \
     --from-literal=LLM_PROVIDER=gemini \
     --from-literal=LOG_LEVEL=INFO
   ```

Update values per environment. Reference them via `envFrom` in Deployments later.

---

## 5. Persistent storage

SABLE stores checkpoints/results under `SABLE_DATA_ROOT` (default `./data`). PostgreSQL and Redis also need storage.

Create PersistentVolumeClaims sized for your workloads (adjust `storageClassName` to match your OpenShift cluster):

```yaml
# postgres-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 20Gi
  storageClassName: managed-premium
---
# redis-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: redis-data
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 5Gi
  storageClassName: managed-premium
---
# sable-artifacts-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: sable-artifacts
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 50Gi
  storageClassName: managed-premium
```

Apply them:
```bash
oc apply -f postgres-pvc.yaml
oc apply -f redis-pvc.yaml
oc apply -f sable-artifacts-pvc.yaml
```

---

## 6. Deploy PostgreSQL and Redis

You can either:
- Use Operators (recommended for production) and provision managed instances, or
- Deploy light-weight StatefulSets yourself.

Example bare deployment (adapt as needed):

```yaml
# postgres.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          ports:
            - containerPort: 5432
        env:
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: sable-secrets
                  key: POSTGRES_USER
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: sable-secrets
                  key: POSTGRES_PASSWORD
            - name: POSTGRES_DB
              valueFrom:
                secretKeyRef:
                  name: sable-secrets
                  key: POSTGRES_DB
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
          livenessProbe:
            exec:
              command: ["/bin/sh", "-c", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
            initialDelaySeconds: 30
            periodSeconds: 10
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: postgres-data
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  ports:
    - port: 5432
      targetPort: 5432
  selector:
    app: postgres
```

Similarly for Redis:

```yaml
# redis.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          command: ["redis-server", "--appendonly", "yes", "--requirepass", "$(REDIS_PASSWORD)"]
          env:
            - name: REDIS_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: sable-secrets
                  key: PREDIS_PASSWORD
          ports:
            - containerPort: 6379
          volumeMounts:
            - name: data
              mountPath: /data
          livenessProbe:
            exec:
              command: ["redis-cli", "-a", "$(REDIS_PASSWORD)", "PING"]
            periodSeconds: 10
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: redis-data
---
apiVersion: v1
kind: Service
metadata:
  name: redis
spec:
  ports:
    - port: 6379
      targetPort: 6379
  selector:
    app: redis
```

Apply both manifests:
```bash
oc apply -f postgres.yaml
oc apply -f redis.yaml
```

---

## 7. Run database migrations

Convert the `migrations` service from `docker-compose.yml` into a Kubernetes Job so it runs once whenever you deploy a new image.

```yaml
# migrations-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: sable-migrations
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: migrations
          image: image-registry.openshift-image-registry.svc:5000/<project>/sable-api:latest
          command: ["/bin/bash", "-lc", "micromamba run -n sable alembic upgrade head"]
          envFrom:
            - configMapRef:
                name: sable-config
            - secretRef:
                name: sable-secrets
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: sable-secrets
                  key: DATABASE_URL
```

Run migrations after updating the image:
```bash
oc apply -f migrations-job.yaml
oc wait --for=condition=complete job/sable-migrations --timeout=180s
```

---

## 8. Deploy the SABLE API

Create a Deployment that points to the OpenShift image stream (or external registry), mounts volumes, and exposes port 8000.

```yaml
# api-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sable-api
spec:
  replicas: 2
  selector:
    matchLabels:
      app: sable-api
  template:
    metadata:
      labels:
        app: sable-api
    spec:
      containers:
        - name: api
          image: image-registry.openshift-image-registry.svc:5000/<project>/sable-api:latest
          ports:
            - containerPort: 8000
          command: ["/bin/bash", "-lc"]
          args:
            - |
              micromamba run -n sable uvicorn server.app:app \
                --host 0.0.0.0 --port 8000
          envFrom:
            - configMapRef:
                name: sable-config
            - secretRef:
                name: sable-secrets
          volumeMounts:
            - name: artifacts
              mountPath: /app/data
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 60
            periodSeconds: 30
          resources:
            requests:
              cpu: "250m"
              memory: "512Mi"
            limits:
              cpu: "1"
              memory: "2Gi"
      volumes:
        - name: artifacts
          persistentVolumeClaim:
            claimName: sable-artifacts
```

Add the Service and Route:

```yaml
# api-service-route.yaml
apiVersion: v1
kind: Service
metadata:
  name: sable-api
spec:
  selector:
    app: sable-api
  ports:
    - port: 8000
      targetPort: 8000
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: sable-api
spec:
  to:
    kind: Service
    name: sable-api
  port:
    targetPort: 8000
  tls:
    termination: edge
```

Apply them:
```bash
oc apply -f api-deployment.yaml
oc apply -f api-service-route.yaml
```

Verify pod status:
```bash
oc get pods -l app=sable-api
oc logs deployment/sable-api
```

---

## 9. Optional: Deploy the frontend

The Vite-based UI ships as a static bundle that Nginx serves from `/usr/share/nginx/html`. You have two broad options:

1. **Build in CI and push to a registry** (recommended for production).
2. **Use an OpenShift BuildConfig** that consumes the `ui/Dockerfile` directly.

Either way, set `VITE_API_BASE` during the build so API calls target your Route. For example:

```bash
# From the repo root
oc new-build --name=sable-frontend --binary --strategy=docker --context-dir=ui

oc set env bc/sable-frontend \
  VITE_API_BASE=https://sable-api-url-<domain>

oc start-build sable-frontend \
  --from-dir=ui \
  --follow

# CI or workstation build
docker build -f ui/Dockerfile -t quay.io/<org>/sable-frontend:2025-11-13 \
  --build-arg VITE_API_BASE=https://api.apps.<cluster-domain>/api .
docker push quay.io/<org>/sable-frontend:2025-11-13

# Or OpenShift build
oc new-build --name=sable-frontend --binary --strategy=docker -f ui/Dockerfile
oc start-build sable-frontend --env VITE_API_BASE=https://api.apps.<cluster-domain>/api --from-dir=ui --follow
```

Because the bundle is baked at build time, changes to `VITE_API_BASE` require a rebuild. If you need dynamic configuration, expose a JSON config file via ConfigMap and fetch it on app bootstrap.

After the image exists in your project, create a Deployment, Service, and Route:

```yaml
# frontend.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sable-frontend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: sable-frontend
  template:
    metadata:
      labels:
        app: sable-frontend
    spec:
      containers:
        - name: frontend
          image: image-registry.openshift-image-registry.svc:5000/<project>/sable-frontend:latest
          ports:
            - containerPort: 8080
          env:
            - name: API_BASE
              value: "https://<your-api-route-host>"
          readinessProbe:
            httpGet:
              path: /
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: sable-frontend
spec:
  selector:
    app: sable-frontend
  ports:
    - port: 80
      targetPort: 8080
---
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: sable-frontend
spec:
  to:
    kind: Service
    name: sable-frontend
  tls:
    termination: edge
```

Apply:
```bash
oc create configmap sable-frontend-nginx --from-file=default.conf=ui/nginx.conf
oc apply -f frontend.yaml
```

### Frontend routing models

- **Separate domain:** point a DNS record (e.g., `sable.example.com`) to the Route host shown by `oc get route sable-frontend`.
- **Subpath under the API domain:** update `nginx.conf` to `location /app/ { try_files ... }` and serve the UI behind the API Route using edge TLS.
- **Single Route, multiple services:** leverage OpenShift’s [alternateBackends](https://docs.openshift.com/container-platform/latest/networking/routes/route-configuration.html#nw-route-specific-annotations_route-configuration) annotations if you want `/api` to hit the API service and `/` to hit the frontend.

### Caching and security headers

Customize `ui/nginx.conf` (or the ConfigMap above) to add:

- `add_header Strict-Transport-Security "max-age=31536000; includeSubdomains" always;`
- `add_header Content-Security-Policy "default-src 'self' ...";`
- Long-lived caching for assets (`location /assets/ { expires 30d; }`) while keeping `index.html` cache-busted (`expires -1;`).

Rebuild the image whenever you change the config so the ConfigMap stays in sync.

### Wiring Auth0 or other IdPs

If you use Auth0, configure the callbacks to match the frontend Route:

- Allowed Callback URLs: `https://<frontend-route-host>/callback`
- Allowed Logout URLs: `https://<frontend-route-host>/`
- Allowed Web Origins: `https://<frontend-route-host>`

Expose the Auth0 tenant ID via `VITE_AUTH0_DOMAIN` and `VITE_AUTH0_CLIENT_ID` at build time (add them to `ui/.env.production` or pass as build args). The React app reads them from `import.meta.env.VITE_*` variables.

### Verifying the deployment

1. Wait for the rollout: `oc rollout status deployment/sable-frontend`.
2. Hit the Route URL; ensure assets load without 404s.
3. Open the browser dev tools to confirm API requests target the expected host and return 200 responses.
4. Optionally run Lighthouse to check performance and best-practice scores.

When updates are ready, rebuild/push the image, update the Deployment image tag (or trigger an ImageStream import), and watch the rollout complete.

---

## 10. Background workflow runner (CLI)

If you need the CLI container for manual jobs (`sable` service in `docker-compose.yml`), replicate it as a CronJob or Deployment that runs ad-hoc tasks. Make sure it shares the same secrets/config and the artifact PVC.

```yaml
# cli-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sable-cli
spec:
  replicas: 1
  selector:
    matchLabels:
      app: sable-cli
  template:
    metadata:
      labels:
        app: sable-cli
    spec:
      containers:
        - name: cli
          image: image-registry.openshift-image-registry.svc:5000/sable/sable-api:latest
          command: ["/bin/bash", "-lc", "sleep infinity"]
          envFrom:
            - configMapRef:
                name: sable-config
            - secretRef:
                name: sable-secrets
          volumeMounts:
            - name: artifacts
              mountPath: /app/data
      volumes:
        - name: artifacts
          persistentVolumeClaim:
            claimName: sable-artifacts
```

You can `oc rsh` into the pod and run `micromamba run -n sable python run_workflow.py ...` when needed.

---

## 11. Smoke tests and validation

1. Confirm migrations succeeded.
2. Hit the API Route (e.g., `curl https://<api-route>/health`).
3. Run the test workflow:
   ```bash
   oc rsh deploy/sable-cli
   micromamba run -n sable python run_workflow.py --example
   ```
4. Access the frontend Route and log in.

---

## 12. Day-2 operations

- **Scaling:** use `oc scale deployment/sable-api --replicas=4`.
- **Rolling updates:** new image pushes trigger rollout; watch with `oc rollout status deployment/sable-api`.
- **Logs:** `oc logs deployment/sable-api` and `oc logs deployment/sable-redis`.
- **Backups:** snapshot PostgreSQL PVCs or use pg_dump; archive `/app/data` artifacts.
- **Secrets rotation:** update values with `oc set env deployment/sable-api --from=secret/sable-secrets --prefix=SECRET_` or reapply the Secret.
- **Monitoring:** integrate OpenShift monitoring/alerts for pod health, HTTP errors, queue sizes.

---

## 13. Automating

For repeatable deployments:
- Wrap manifests in Kustomize or Helm.
- Set up a CI pipeline (GitHub Actions, Tekton) to build images, run tests, push to registry, and apply manifests.
- Use OpenShift GitOps/ArgoCD for declarative environments (dev/stage/prod).

---

## 14. Clean up

```bash
oc delete all -l app=sable-api
oc delete all -l app=sable-frontend
oc delete all -l app=postgres
oc delete all -l app=redis
oc delete pvc postgres-data redis-data sable-artifacts
oc delete secret sable-secrets postgres-credentials redis-credentials
oc delete configmap sable-config
```

---
