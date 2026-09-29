# Annunciator working agreements

Read README.md, AI-ONBOARDING.md and docs/FIRST-RUN.md before changes.
Check package.json name=annunciator-public and use only this checkout plus folders the current user explicitly provides.
The application has no preset machines or accounts. Obtain all targets and credentials from the current user.
Keep runtime/, server/config.local.json, keys, builds and credentials out of source archives and commits.
Never infer permission to reboot a machine from permission to monitor it. Verify target ownership before applying generated target scripts.
Jev is optional. It sends facts to the user's OpenRouter account and advises where to store them; it never writes memory automatically.
Run tests, distribution audit and a clean install check before sharing changes.
Take a fresh display snapshot immediately before GUI input and verify the visible result afterward.
