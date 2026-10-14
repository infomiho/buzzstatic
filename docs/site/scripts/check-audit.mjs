import { spawnSync } from 'node:child_process';

// braces has no fixed release. It only expands glob patterns written in this repository at build time.
const acceptedAdvisories = new Set(['https://github.com/advisories/GHSA-vfj7-8cjw-p6xm']);

function auditReport() {
	const audit = spawnSync('npm', ['audit', '--omit=dev', '--json'], { encoding: 'utf8' });
	if (!audit.stdout) {
		throw new Error(`npm audit produced no report: ${audit.stderr}`);
	}
	return JSON.parse(audit.stdout);
}

function advisories(report) {
	return Object.values(report.vulnerabilities ?? {})
		.flatMap((vulnerability) => vulnerability.via)
		.filter((source) => typeof source === 'object');
}

const unaccepted = new Map(
	advisories(auditReport())
		.filter((advisory) => !acceptedAdvisories.has(advisory.url))
		.map((advisory) => [advisory.url, advisory]),
);

for (const advisory of unaccepted.values()) {
	console.error(`${advisory.severity}: ${advisory.name} ${advisory.range} ${advisory.title} ${advisory.url}`);
}

if (unaccepted.size > 0) {
	process.exit(1);
}
console.log('npm audit: no unaccepted advisories.');
