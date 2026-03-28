import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

x = np.linspace(0, 2 * np.pi, 500)
y = np.sin(x)

fig, ax = plt.subplots(figsize=(10, 4), facecolor='#111827')
ax.set_facecolor('#111827')
ax.plot(x, y, color='#4f9cf9', linewidth=2)
ax.set_title('Sine Wave', color='white', fontsize=14, pad=10)
ax.set_xlabel('x', color='#aaa')
ax.set_ylabel('sin(x)', color='#aaa')
ax.tick_params(colors='#aaa')
ax.grid(color='#333', linestyle='--', linewidth=0.5)
for spine in ax.spines.values():
    spine.set_edgecolor('#333')
plt.tight_layout()
plt.savefig(r'C:/Users/ioanc/github/light_cc/sine_wave.png', dpi=150)
