const CHECK_NAME = "e2e";

const marker = (environment) => `<!-- preview-environment:${environment} -->`;

function e2eState(env) {
  if (env.EXACT_IMAGE !== "true") {
    return { label: "waiting for the pushed image", check: { status: "queued" } };
  }
  const passed = env.E2E_OUTCOME === "success";
  return {
    label: passed ? "passed" : "failed",
    check: { status: "completed", conclusion: passed ? "success" : "failure" },
  };
}

function reportBody(result, timings, label) {
  const urls = Object.entries(result.urls).map(([service, url]) => `- ${service}: ${url}`);
  const images = Object.entries(result.images).map(
    ([service, image]) => `| ${service} | \`${image.tag}\` | ${image.source} |`,
  );
  const stages = timings.flatMap((record) =>
    Object.entries(record.stages).map(
      ([stage, seconds]) => `| ${record.command} | ${stage} | ${seconds.toFixed(2)} |`,
    ),
  );
  return [
    marker(result.environment),
    `### Preview environment \`${result.environment}\``,
    "",
    `E2E: **${label}**`,
    "",
    ...urls,
    "",
    "| Service | Image | Source |",
    "|---|---|---|",
    ...images,
    "",
    `Dataset version: \`${result.dataset_version}\``,
    "",
    "| Command | Stage | Seconds |",
    "|---|---|---|",
    ...stages,
  ].join("\n");
}

async function upsertPullRequestComments(github, owner, repo, sha, environment, body) {
  const { data: pulls } = await github.rest.repos.listPullRequestsAssociatedWithCommit({
    owner,
    repo,
    commit_sha: sha,
  });
  for (const pull of pulls.filter((candidate) => candidate.state === "open")) {
    const comments = await github.paginate(github.rest.issues.listComments, {
      owner,
      repo,
      issue_number: pull.number,
      per_page: 100,
    });
    const existing = comments.find((comment) => comment.body?.includes(marker(environment)));
    if (existing) {
      await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
    } else {
      await github.rest.issues.createComment({ owner, repo, issue_number: pull.number, body });
    }
  }
}

async function report({ github, context, env = process.env }) {
  const { owner, repo } = context.repo;
  const result = JSON.parse(env.RESULT);
  const timings = [env.DEPLOY_TIMINGS, env.E2E_TIMINGS].filter(Boolean).map(JSON.parse);
  const state = e2eState(env);
  const body = reportBody(result, timings, state.label);
  await github.rest.checks.create({
    owner,
    repo,
    name: CHECK_NAME,
    head_sha: env.SHA,
    ...state.check,
    output: { title: `E2E ${state.label} on preview-${result.environment}`, summary: body },
  });
  await upsertPullRequestComments(github, owner, repo, env.SHA, result.environment, body);
}

async function deactivate({ github, context, env = process.env }) {
  const { owner, repo } = context.repo;
  const deployments = await github.paginate(github.rest.repos.listDeployments, {
    owner,
    repo,
    environment: `preview-${env.ENVIRONMENT}`,
    per_page: 100,
  });
  for (const deployment of deployments) {
    await github.rest.repos.createDeploymentStatus({
      owner,
      repo,
      deployment_id: deployment.id,
      state: "inactive",
    });
  }
}

module.exports = { report, deactivate, reportBody };
