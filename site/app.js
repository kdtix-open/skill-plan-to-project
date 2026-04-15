const commandMap = {
  "home-skill": `uvx --from git+https://github.com/kdtix-open/skill-plan-to-project \\
  plan-to-project-install --destination home-skill`,
  "home-plugin": `uvx --from git+https://github.com/kdtix-open/skill-plan-to-project \\
  plan-to-project-install --destination home-plugin`,
  "repo-plugin": `uvx --from git+https://github.com/kdtix-open/skill-plan-to-project \\
  plan-to-project-install --destination repo-plugin --repo-root /path/to/repo`,
};

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
