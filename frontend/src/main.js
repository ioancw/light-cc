import { mount } from 'svelte';
import App from './App.svelte';
import './styles/global.css';
import { installViewportHeight } from './lib/viewport-height.js';

installViewportHeight();

const app = mount(App, { target: document.getElementById('app') });

export default app;
