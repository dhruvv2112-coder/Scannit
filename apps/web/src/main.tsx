import { createRoot } from 'react-dom/client';
import App from './App';
import './style.css';

function hasWebGL2(): boolean {
  try { return !!document.createElement('canvas').getContext('webgl2'); } catch { return false; }
}

const root = createRoot(document.getElementById('root')!);
root.render(
  hasWebGL2() ? <App /> : (
    <main className="root" style={{ padding: 24 }} data-testid="fallback">
      <h1>3D view unavailable</h1>
      <p>This browser or device does not provide WebGL2, which the viewer needs. Try a current Chrome, Edge, Firefox or Safari, or enable hardware acceleration.</p>
    </main>
  ),
);
