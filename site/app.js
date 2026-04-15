const commandMap = {
  "home-skill": `uvx --from git+https://github.com/kdtix-open/skill-plan-to-project \\
  plan-to-project-install --destination home-skill`,
  "home-plugin": `uvx --from git+https://github.com/kdtix-open/skill-plan-to-project \\
  plan-to-project-install --destination home-plugin`,
  "repo-plugin": `uvx --from git+https://github.com/kdtix-open/skill-plan-to-project \\
  plan-to-project-install --destination repo-plugin --repo-root /path/to/repo`,
};

const releaseApiUrl =
  "https://api.github.com/repos/kdtix-open/skill-plan-to-project/releases?per_page=1";

async function copyCommand(target) {
  const text = commandMap[target];
  if (!text) {
    return;
  }

  await navigator.clipboard.writeText(text);
}

for (const button of document.querySelectorAll("[data-copy-target]")) {
  button.addEventListener("click", async () => {
    const originalLabel = button.textContent;
    try {
      await copyCommand(button.dataset.copyTarget);
      button.textContent = "Copied";
    } catch {
      button.textContent = "Copy failed";
    }

    window.setTimeout(() => {
      button.textContent = originalLabel;
    }, 1600);
  });
}

async function hydrateLatestRelease() {
  const title = document.getElementById("release-title");
  const summary = document.getElementById("release-summary");

  if (!title || !summary) {
    return;
  }

  try {
    const response = await fetch(releaseApiUrl, {
      headers: { Accept: "application/vnd.github+json" },
    });

    if (!response.ok) {
      return;
    }

    const releases = await response.json();
    const [release] = Array.isArray(releases) ? releases : [];

    if (!release) {
      return;
    }

    const name = release.name || release.tag_name;
    const publishedAt = release.published_at
      ? new Date(release.published_at).toLocaleDateString("en-US", {
          year: "numeric",
          month: "short",
          day: "numeric",
        })
      : "recently";

    title.textContent = `Latest GitHub release: ${name}`;
    summary.textContent = `Published ${publishedAt}. Read the generated GitHub release notes for the tagged build, then use CHANGELOG.md for the human-authored operator summary and RELEASING.md for the maintainer workflow.`;
  } catch {
    // Keep the static fallback copy when the GitHub API is unavailable.
  }
}

hydrateLatestRelease();
