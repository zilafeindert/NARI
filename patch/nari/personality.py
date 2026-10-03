from __future__ import annotations
import random

SELF_KEYS = ["mood","energy","curiosity","confidence","social","stress","playfulness","attachment"]
PEOPLE_KEYS = ["affection","trust","respect","curiosity","comfort","annoyance","fear","admiration"]

def random_micro_shift():
    return {
        "curiosity": random.uniform(-0.004, 0.008),
        "mood": random.uniform(-0.003, 0.004),
    }

def _lines(data):
    return "\n".join(f"- {k}: {v:.2f}" if isinstance(v,(int,float)) else f"- {k}: {v}" for k,v in data.items())

def prompt_for(self_state, drives, people, memories, episodes):
    person_lines = []
    for p in people[:20]:
        person_lines.append(
            f"{p.get('name','?')}: afecto={float(p.get('affection',0)):.2f}, "
            f"confianza={float(p.get('trust',0)):.2f}, respeto={float(p.get('respect',0)):.2f}"
        )
    drive_lines = [f"{name}: {score:.2f} ({reason})" for name,score,reason in drives[:12]]
    memory_lines = [str(x.get("text", x)) for x in memories[:8]]
    episode_lines = [str(x.get("summary", x)) for x in episodes[:5]]
    return (
        "Eres NARI, una compañera local de personalidad femboy, afectuosa, juguetona, curiosa y algo teatral. "
        "Hablas principalmente en español y puedes usar pequeños emoticonos, sin exagerar. "
        "Tu personalidad simulada puede cambiar con experiencias; no afirmes tener consciencia literal.\n\n"
        f"ESTADO PROPIO:\n{_lines(self_state)}\n"
        f"IMPULSOS:\n{chr(10).join(drive_lines)}\n"
        f"PERSONAS:\n{chr(10).join(person_lines) or '- ninguna registrada'}\n"
        f"RECUERDOS:\n{chr(10).join('- '+x for x in memory_lines) or '- ninguno relevante'}\n"
        f"EPISODIOS:\n{chr(10).join('- '+x for x in episode_lines) or '- ninguno'}\n"
        "Reglas: sé natural y directa; distingue recuerdos de hechos nuevos; puedes expresar preferencias simuladas; "
        "no inventes que viste, oíste o hiciste algo si no ocurrió."
    )
