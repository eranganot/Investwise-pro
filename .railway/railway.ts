// Railway infrastructure-as-code for the Investwise-pro service (plan v3 PR 119).
//
// Replaces railway.json (Config as Code, which stops working 2026-12-01). Every
// setting railway.json carried is here, in Railway's typed `build` / `deploy`
// objects (railway/iac `BuildConfig` / `DeployConfig`). `railway config migrate`
// emitted only start / healthcheck / preDeploy and left the builder as a
// COMMENT -- the service's own setting is RAILPACK, so a straight migrate would
// have built the app without our Dockerfile and dropped the restart policy and
// the pre-deploy timeout.
//
// Railway does not read this file on deploy: it is applied with
// `railway config apply`, and `railway config plan` must show no changes once
// it is (tests/test_the_deploy_runs_the_migrations.py pins its contents).
import { defineRailway, github, preserve, project, service } from "railway/iac";

// One service in a project that also holds Postgres and its volume, which this
// file does not manage.
export const partial = "Investwise-pro";

export default defineRailway(() => {
  const investwise = service("Investwise-pro", {
    // Deploys on push to main, as before. Declared, or `apply` would detach the repo.
    source: github("eranganot/Investwise-pro", { branch: "main", checkSuites: false }),
    // The service's variables are managed in Railway, NOT here. Each is listed
    // with preserve() so `apply` keeps its value untouched; an unlisted
    // variable would be DELETED (the first plan proposed deleting all 32).
    // Values never appear in this file. Add a name here before adding the
    // variable in Railway, or the next apply removes it.
    variables: {
      ADVERSARY_LLM_ENABLED: preserve(),
      AGENT_API_KEY: preserve(),
      ASSUMPTION_RETURN_BASIS: preserve(),
      AUTH_PASSWORD: preserve(),
      CGT_RATE: preserve(),
      DATABASE_URL: preserve(),
      DEBUG: preserve(),
      ENVIRONMENT: preserve(),
      FMP_API_KEY: preserve(),
      FX_PROVIDER: preserve(),
      GOOGLE_API_KEY: preserve(),
      GOOGLE_OAUTH_CLIENT_ID: preserve(),
      GOOGLE_OAUTH_CLIENT_SECRET: preserve(),
      JWT_SECRET: preserve(),
      MARKET_DATA_PROVIDER: preserve(),
      MAX_DRAWDOWN_CAP: preserve(),
      MIN_CONFIDENCE: preserve(),
      MIN_IMPACT_SCORE: preserve(),
      MONTE_CARLO_RUNS: preserve(),
      PORT: preserve(),
      REGIME_GATE_ENABLED_FOR: preserve(),
      REGIME_GATE_MAX_CAGR_GIVEUP_PCT: preserve(),
      REQUIRE_AUTH: preserve(),
      SUPERADMIN_NAME: preserve(),
      SURTAX_RATE: preserve(),
      SURTAX_THRESHOLD_ILS: preserve(),
      TAX_YEAR: preserve(),
      VOLATILITY_CAP: preserve(),
      adversary_llm_model: preserve(),
      vapid_private_key: preserve(),
      vapid_public_key: preserve(),
    },
    build: {
      builder: "DOCKERFILE",
      dockerfilePath: "Dockerfile",
    },
    deploy: {
      startCommand: "uvicorn app.main:app --host 0.0.0.0 --port 8000",
      // The schema is owned by the migrations: a failed upgrade stops the deploy.
      preDeployCommand: ["alembic upgrade head && alembic current"],
      preDeployTimeoutSeconds: 300,
      // A container that never serves /health/ready never takes traffic.
      healthcheckPath: "/health/ready",
      healthcheckTimeout: 300,
      // Restart on failure, 3 retries. ON_FAILURE is Railway's DEFAULT policy and
      // its API stores the default as null, so declaring `restartPolicyType`
      // leaves a permanent phantom diff in `railway config plan`. Verified on
      // deployment 73db81bd (2026-10-11): its manifest reads ON_FAILURE / 3.
      restartPolicyMaxRetries: 3,
    },
  });
  return project("daring-transformation", {
    resources: [investwise],
  });
});
