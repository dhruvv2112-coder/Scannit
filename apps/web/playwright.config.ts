import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '../../tests/viewer/e2e',
  timeout: 60_000,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:4173', viewport: { width: 1280, height: 800 } },
  projects: [{
    name: 'chromium',
    use: { ...devices['Desktop Chrome'], launchOptions: { executablePath: process.env.PW_CHROME_PATH || undefined, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] } },
  }],
  webServer: { command: 'npm run preview', url: 'http://localhost:4173', reuseExistingServer: true, timeout: 60_000 },
});
