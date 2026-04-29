<div align="center">
  <a href="index.md">← Índice</a> &nbsp;·&nbsp;
  <a href="../en/config.md">🇬🇧 Read in English</a>
</div>

<br>

# Configuración

Toda la configuración se realiza mediante variables de entorno. Estas se establecen desde el orquestador de despliegue ([iAgentsHub](https://github.com/iagentshub/iagentshub)) y no requieren modificar ningún fichero de código.

---

## Qué se puede configurar

| Ajuste | Descripción |
|---|---|
| Secreto de sesión | Clave usada para firmar los tokens de sesión. Obligatorio en producción. |
| Directorio de datos | Ruta donde se almacenan agentes, conexiones, skills y memoria. |
| Puerto y host | Dónde escucha el servidor. |
| Orígenes permitidos | Dominios desde los que se permite el acceso a la API. |
| Duración de sesión | Tiempo en horas que una sesión permanece activa. |
| Google OAuth | Credenciales para activar el inicio de sesión con Google. |
| Restricción de acceso | Limitar el acceso a correos o dominios específicos de Google. |

---

## Acceso con Google

El inicio de sesión con Google requiere registrar la aplicación en Google Cloud Console y configurar las credenciales obtenidas. Una vez configurado, cualquier cuenta de Google puede acceder, a menos que se restrinja el acceso a correos o dominios específicos.

---

## Secreto de sesión

Debe generarse de forma aleatoria antes del primer arranque y no cambiarse mientras haya sesiones activas. Si no se configura, el sistema usa un valor almacenado en los datos de la plataforma — aceptable en desarrollo, no en producción.
