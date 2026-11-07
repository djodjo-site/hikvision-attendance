import uvicorn
import sys
import os

# Ajout du dossier src dans le chemin d'exécution Python
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

if __name__ == "__main__":
    print("==================================================")
    print("  Démarrage du serveur de pointage Hikvision...   ")
    print("  En écoute des événements (Webhooks / ISAPI)     ")
    print("==================================================")
    
    # Lancement du serveur Uvicorn pointant vers l'application FastAPI
    uvicorn.run(
        "src.listener_isup:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True
    )