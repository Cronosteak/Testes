import numpy as np
import matplotlib.pyplot as plt

def tri(x,a,b,c):
    return np.maximum(np.minimum((x-a)/(b-a+1e-9),(c-x)/(c-b+1e-9)),0.0)

# --- 1) Error de posición (A→B), 0–5 m ---
x_pos = np.linspace(0, 5.0, 400)
centros_pos = np.linspace(0.0, 5.0, 5)   # 5 conjuntos
ancho_pos = 1.1

plt.figure(figsize=(7,4))
for c in centros_pos:
    y = tri(x_pos, c-ancho_pos/2, c, c+ancho_pos/2)
    plt.plot(x_pos, y)
plt.title("Funciones de pertenencia — error de posición (A→B)")
plt.xlabel("Distancia al objetivo (m)")
plt.ylabel("Grado de pertenencia")
plt.grid(True, alpha=0.3); plt.ylim(-0.05,1.05); plt.tight_layout()
plt.savefig("membership_error_pos.png", dpi=300)

# --- 2) Error de orientación (pitch/roll), ±20° ---
x_ori = np.linspace(-20, 20, 400)
centros_ori = np.linspace(-20, 20, 5)
ancho_ori = 10

plt.figure(figsize=(7,4))
for c in centros_ori:
    y = tri(x_ori, c-ancho_ori/2, c, c+ancho_ori/2)
    plt.plot(x_ori, y)
plt.title("Funciones de pertenencia — error de orientación (pitch/roll)")
plt.xlabel("Orientación (grados)")
plt.ylabel("Grado de pertenencia")
plt.grid(True, alpha=0.3); plt.ylim(-0.05,1.05); plt.tight_layout()
plt.savefig("membership_error_orientation.png", dpi=300)
