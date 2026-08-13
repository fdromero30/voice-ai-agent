
// ============================================================================
// CONSTANTES Y ESTADO GLOBAL
// ============================================================================
const API_URL = '/api/voice-chat';  // Backend FastAPI (mismo origen)

// Elementos DOM
const recordBtn = document.getElementById('recordBtn');
const micIcon = document.getElementById('micIcon');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const recordingWave = document.getElementById('recordingWave');
const recordingRing = document.getElementById('recordingRing');
const btnHint = document.getElementById('btnHint');
const timerContainer = document.getElementById('timerContainer');
const timerEl = document.getElementById('timer');
const playerContainer = document.getElementById('playerContainer');
const audioPlayer = document.getElementById('audioPlayer');
const qrContainer = document.getElementById('qrContainer');
const qrImage = document.getElementById('qrImage');
const orderDashboard = document.getElementById('orderDashboard');
const orderProductsList = document.getElementById('orderProductsList');
const orderSubtotal = document.getElementById('orderSubtotal');
const orderAddress = document.getElementById('orderAddress');
const orderCity = document.getElementById('orderCity');
const orderShipping = document.getElementById('orderShipping');
const orderTotal = document.getElementById('orderTotal');
const errorContainer = document.getElementById('errorContainer');
const errorText = document.getElementById('errorText');

// Estado de grabación
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let isProcessing = false;
let isMicBusy = false;          // getUserMedia en curso / MediaRecorder en uso
let shouldStopAfterStart = false; // usuario soltó el botón antes de que arrancara
let timerInterval = null;
let startTime = 0;

// Detección de silencio (2 segundos de silencio → detener grabación)
let audioContext = null;
let analyser = null;
let silenceTimer = null;
let silenceThreshold = -50;  // dB
let silenceDuration = 2000;  // 2 segundos

// Memoria de conversación: acumula el historial de mensajes
// para que el LLM recuerde el contexto (nombre, producto, etc.)
let conversationHistory = [];

// ============================================================================
// FUNCIONES DE ESTADO / UI
// ============================================================================

/**
 * Actualiza el indicador de estado visual.
 * @param {string} text     - Texto a mostrar
 * @param {string} color    - Color del punto indicador
 * @param {boolean} wave    - Mostrar onda de grabación
 */
function setStatus(text, color = 'bg-slate-500', wave = false) {
    statusText.textContent = text;
    statusDot.className = `w-3 h-3 rounded-full ${color} transition-all duration-300`;
    recordingWave.classList.toggle('hidden', !wave);
}

/**
 * Muestra un mensaje de error.
 */
function showError(message) {
    errorText.textContent = message;
    errorContainer.classList.remove('hidden');
    // Auto-ocultar después de 5 segundos
    setTimeout(() => errorContainer.classList.add('hidden'), 5000);
}

/**
 * Actualiza el temporizador de grabación.
 */
function updateTimer() {
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const minutes = Math.floor(elapsed / 60).toString().padStart(2, '0');
    const seconds = (elapsed % 60).toString().padStart(2, '0');
    timerEl.textContent = `${minutes}:${seconds}`;
}

// ============================================================================
// LÓGICA DE GRABACIÓN (MediaRecorder API)
// ============================================================================

/**
 * Inicia la grabación del micrófono.
 * Maneja la asincronía de getUserMedia: si el usuario suelta el botón
 * antes de que el MediaRecorder arranque, se cancela la grabación
 * y NO se envía audio vacío.
 */
async function startRecording() {
    if (isMicBusy || isProcessing || isRecording) {
        return;
    }
    isMicBusy = true;
    shouldStopAfterStart = false;
    setStatus('Preparando micrófono...', 'bg-amber-500', false);

    try {
        // Solicitar acceso al micrófono (asíncrono)
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

        // Si el usuario YA soltó el botón mientras cargaba el micrófono,
        // cancelamos sin grabar (no enviar audio vacío)
        if (shouldStopAfterStart) {
            shouldStopAfterStart = false;
            stream.getTracks().forEach(track => track.stop());
            isMicBusy = false;
            setStateIdle();
            return;
        }

        // Crear MediaRecorder - usar webm/supported mime type
        const mimeType = MediaRecorder.isTypeSupported('audio/webm')
            ? 'audio/webm'
            : 'audio/webm;codecs=opus';
        mediaRecorder = new MediaRecorder(stream, { mimeType });

        audioChunks = [];
        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) audioChunks.push(event.data);
        };

        mediaRecorder.onstop = async () => {
            // Detener todas las pistas del stream
            stream.getTracks().forEach(track => track.stop());
            isMicBusy = false;
            isRecording = false;
            setStateProcessing();
            // Esperar 200ms para que el último chunk de audio se procese
            // completamente (evita blob incompleto que Groq rechaza)
            setTimeout(() => sendAudioToBackend(), 200);
        };

        // MODO CORREGIDO DE SINCRONÍA:
        // Usar timeslice (250ms) obliga al MediaRecorder a emitir chunks
        // continuamente. Al detener, el último dataavailable SIEMPRE
        // contiene el audio capturado hasta ese momento, evitando blob
        // incompleto/vacío que Groq rechaza con "invalid media file".
        mediaRecorder.start(250);

        mediaRecorder.onerror = () => {
            isMicBusy = false;
            isRecording = false;
            shouldStopAfterStart = false;
            setStateIdle();
            showError('Error en el grabador de audio.');
        };

        // El estado de grabación se activa solo cuando arrancó
        isRecording = true;
        isMicBusy = false;
        setStateRecording();

        // Configurar detección de silencio: si el usuario deja de hablar
        // por 2 segundos, se detiene la grabación automáticamente
        setupSilenceDetection(stream);

        // Si el usuario soltó MUY rápido justo después de arrancar,
        // detener inmediatamente (pero con al menos algo de audio)
        if (shouldStopAfterStart) {
            shouldStopAfterStart = false;
            stopRecording();
        }
    } catch (error) {
        console.error('Error al acceder al micrófono:', error);
        isMicBusy = false;
        shouldStopAfterStart = false;
        setStateIdle();
        showError('No se pudo acceder al micrófono. Verifica los permisos del navegador.');
    }
}

/**
 * Detiene la grabación.
 * Si el MediaRecorder aún no arrancó (getUserMedia en curso),
 * marca que debe detenerse al arrancar (cancelación limpia).
 */
function stopRecording() {
    if (mediaRecorder && isRecording) {
        clearSilenceDetection();
        mediaRecorder.stop();
    } else if (isMicBusy) {
        // getUserMedia todavía en curso → cancelar cuando arranque
        shouldStopAfterStart = true;
    }
    // Si no hay nada grabando, no hacemos nada
}

/**
 * Configura la detección de silencio usando Web Audio API.
 * Si el usuario deja de hablar por 2 segundos, se detiene la grabación.
 */
function setupSilenceDetection(stream) {
    try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        const source = audioContext.createMediaStreamSource(stream);
        source.connect(analyser);

        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        const checkSilence = () => {
            if (!isRecording || !analyser) return;

            analyser.getByteFrequencyData(dataArray);
            let sum = 0;
            for (let i = 0; i < bufferLength; i++) {
                sum += dataArray[i];
            }
            const average = sum / bufferLength;
            // Convertir a dB (0-255 → -100 a 0 dB aproximadamente)
            const volume = (average / 255) * 100;

            if (volume < 5) {
                // Silencio detectado
                if (!silenceTimer) {
                    silenceTimer = setTimeout(() => {
                        if (isRecording) {
                            setStatus('Silencio detectado, enviando...', 'bg-amber-500', false);
                            stopRecording();
                        }
                    }, silenceDuration);
                }
            } else {
                // Se detectó sonido, reiniciar el temporizador de silencio
                if (silenceTimer) {
                    clearTimeout(silenceTimer);
                    silenceTimer = null;
                }
            }

            // Continuar monitoreando
            if (isRecording) {
                requestAnimationFrame(checkSilence);
            }
        };

        requestAnimationFrame(checkSilence);
    } catch (error) {
        console.warn('No se pudo configurar detección de silencio:', error);
    }
}

/**
 * Limpia los recursos de detección de silencio.
 */
function clearSilenceDetection() {
    if (silenceTimer) {
        clearTimeout(silenceTimer);
        silenceTimer = null;
    }
    if (audioContext) {
        try {
            audioContext.close();
        } catch (e) {
            // Ignorar errores al cerrar el contexto
        }
        audioContext = null;
    }
    analyser = null;
}

// ============================================================================
// CONVERSIÓN DE AUDIO A WAV (para compatibilidad con Whisper de Groq)
// ============================================================================

/**
 * Convierte un Blob de audio (webm/opus) a WAV PCM 16-bit mono.
 * Groq/Whisper a veces no acepta webm del navegador; WAV es 100% compatible.
 */
function convertBlobToWav(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = async (event) => {
            try {
                const audioContext = new (window.AudioContext || window.webkitAudioContext)();
                const arrayBuffer = event.target.result;
                const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

                // Convertir a mono 16-bit PCM
                const numChannels = 1;
                const sampleRate = audioBuffer.sampleRate;
                const numFrames = audioBuffer.length;
                const bytesPerSample = 2;
                const buffer = new ArrayBuffer(44 + numFrames * bytesPerSample);
                const view = new DataView(buffer);

                // Cabecera WAV
                writeString(view, 0, 'RIFF');
                view.setUint32(4, 36 + numFrames * bytesPerSample, true);
                writeString(view, 8, 'WAVE');
                writeString(view, 12, 'fmt ');
                view.setUint32(16, 16, true);          // Tamaño del chunk fmt
                view.setUint16(20, 1, true);           // PCM
                view.setUint16(22, numChannels, true); // Mono
                view.setUint32(24, sampleRate, true);
                view.setUint32(28, sampleRate * numChannels * bytesPerSample, true);
                view.setUint16(32, numChannels * bytesPerSample, true);
                view.setUint16(34, 16, true);          // Bits por muestra
                writeString(view, 36, 'data');
                view.setUint32(40, numFrames * bytesPerSample, true);

                // Datos PCM
                const channelData = audioBuffer.getChannelData(0);
                let offset = 44;
                for (let i = 0; i < numFrames; i++) {
                    const sample = Math.max(-1, Math.min(1, channelData[i]));
                    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7FFF, true);
                    offset += bytesPerSample;
                }

                audioContext.close();
                resolve(new Blob([buffer], { type: 'audio/wav' }));
            } catch (error) {
                reject(error);
            }
        };
        reader.onerror = reject;
        reader.readAsArrayBuffer(blob);
    });
}

/**
 * Escribe una cadena en un DataView (para cabecera WAV).
 */
function writeString(view, offset, str) {
    for (let i = 0; i < str.length; i++) {
        view.setUint8(offset + i, str.charCodeAt(i));
    }
}

/**
 * Envía el audio grabado al backend y reproduce la respuesta.
 */
async function sendAudioToBackend() {
    if (audioChunks.length === 0) {
        showError('No se capturó audio. Intenta de nuevo.');
        setStateIdle();
        return;
    }

    // Crear Blob de audio original (webm/opus del navegador)
    const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });

    // Convertir a WAV para máxima compatibilidad con Whisper de Groq
    let formData;
    try {
        const wavBlob = await convertBlobToWav(audioBlob);
        formData = new FormData();
        formData.append('voice', wavBlob, 'nota.wav');
    } catch (conversionError) {
        console.error('Error convirtiendo a WAV, se envía el webm original:', conversionError);
        formData = new FormData();
        formData.append('voice', audioBlob, 'nota.webm');
    }

    // Enviar el historial de conversación para que el LLM recuerde el contexto
    if (conversationHistory.length > 0) {
        formData.append('history', JSON.stringify(conversationHistory));
    }

    try {
        // Petición POST al backend
        const response = await fetch(API_URL, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            // Intentar obtener el detalle del error
            let errorMsg = `Error del servidor (${response.status})`;
            try {
                const errorData = await response.json();
                if (errorData.detail) errorMsg = errorData.detail;
            } catch (_) { /* respuesta no JSON */ }
            throw new Error(errorMsg);
        }

        // Obtener las transcripciones de los headers para acumular el historial
        const userTextFromServer = decodeURIComponent(response.headers.get('X-User-Text') || '');
        const assistantTextFromServer = decodeURIComponent(response.headers.get('X-Assistant-Text') || '');

        // Mostrar los datos del pedido en el dashboard derecho
        // (paso a paso: primero productos, luego dirección+total)
        const orderDataHeader = response.headers.get('X-Order-Data');
        if (orderDataHeader) {
            try {
                const order = JSON.parse(decodeURIComponent(orderDataHeader));
                // Mostrar lista de productos
                if (order.productos && order.productos.length > 0) {
                    orderProductsList.innerHTML = '';
                    order.productos.forEach(p => {
                        const div = document.createElement('div');
                        div.className = 'flex justify-between text-sm';
                        div.innerHTML = `<span class="text-slate-800">${p.cantidad}x ${p.producto}</span><span class="text-slate-600">$${p.subtotal.toLocaleString()} ${order.moneda || 'COP'}</span>`;
                        orderProductsList.appendChild(div);
                    });
                }
                orderSubtotal.textContent = `$${(order.subtotal || 0).toLocaleString()} ${order.moneda || 'COP'}`;
                orderAddress.textContent = order.direccion || '-';
                orderCity.textContent = order.ciudad || '-';
                // En el paso 1 (producto+cantidad) envío/total aún no existen → mostrar "-"
                if (order.envio !== null && order.envio !== undefined && order.envio !== '') {
                    orderShipping.textContent = `$${Number(order.envio).toLocaleString()} ${order.moneda || 'COP'}`;
                } else {
                    orderShipping.textContent = 'Pendiente';
                }
                if (order.total !== null && order.total !== undefined && order.total !== '') {
                    orderTotal.textContent = `$${Number(order.total).toLocaleString()} ${order.moneda || 'COP'}`;
                } else {
                    orderTotal.textContent = 'Pendiente';
                }
                orderDashboard.classList.remove('hidden');
            } catch (orderError) {
                console.error('Error parseando datos del pedido:', orderError);
            }
        }

        // Si la respuesta incluye QR de pago, mostrarlo
        const paymentQrUrl = response.headers.get('X-Payment-QR');
        if (paymentQrUrl) {
            qrImage.src = paymentQrUrl;
            qrContainer.classList.remove('hidden');
        } else {
            qrContainer.classList.add('hidden');
        }

        // Acumular historia de la conversación (memoria del contexto)
        if (userTextFromServer) {
            conversationHistory.push({ role: 'user', content: userTextFromServer });
        }
        if (assistantTextFromServer) {
            conversationHistory.push({ role: 'assistant', content: assistantTextFromServer });
        }
        // Mantener solo las últimas 20 interacciones
        if (conversationHistory.length > 20) {
            conversationHistory = conversationHistory.slice(-20);
        }

        // Obtener el audio MP3 de respuesta
        const audioBlobResponse = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlobResponse);

        // Configurar y reproducir
        audioPlayer.src = audioUrl;
        playerContainer.classList.remove('hidden');
        setStatePlaying();

        // Escuchar el evento de reproducción
        audioPlayer.onended = () => setStateIdle();
        audioPlayer.onpause = () => {
            if (audioPlayer.currentTime >= audioPlayer.duration - 0.1) {
                setStateIdle();
            } else {
                // Manual pause - keep playing state but allow user control
                setStateIdle();
            }
        };

        await audioPlayer.play();

    } catch (error) {
        console.error('Error en la petición:', error);
        showError(error.message || 'Error al procesar la nota de voz.');
        setStateIdle();
    }
}

// ============================================================================
// GESTIÓN DE ESTADOS DE LA UI
// ============================================================================

/** Estado: listo (idle) */
function setStateIdle() {
    isProcessing = false;
    recordBtn.disabled = false;
    recordBtn.classList.remove('bg-gradient-to-br', 'from-red-500', 'to-red-600', 'hover:from-red-600', 'hover:to-red-700');
    recordBtn.classList.add('bg-gradient-to-br', 'from-indigo-500', 'to-purple-600', 'hover:from-indigo-600', 'hover:to-purple-700');
    micIcon.classList.remove('fa-stop');
    micIcon.classList.add('fa-microphone');
    recordingRing.classList.add('hidden');
    recordingRing.classList.remove('opacity-100');
    recordingWave.classList.add('hidden');
    timerContainer.classList.add('hidden');
    btnHint.textContent = 'Mantén presionado para hablar';
    setStatus('Listo para hablar', 'bg-slate-500', false);
    clearInterval(timerInterval);
}

/** Estado: grabando */
function setStateRecording() {
    recordBtn.classList.remove('bg-gradient-to-br', 'from-indigo-500', 'to-purple-600', 'hover:from-indigo-600', 'hover:to-purple-700');
    recordBtn.classList.add('bg-gradient-to-br', 'from-red-500', 'to-red-600', 'hover:from-red-600', 'hover:to-red-700');
    micIcon.classList.remove('fa-microphone');
    micIcon.classList.add('fa-stop');
    recordingRing.classList.remove('hidden');
    recordingRing.classList.add('opacity-100', 'animate-ping');
    timerContainer.classList.remove('hidden');
    btnHint.textContent = 'Suelta para enviar';
    setStatus('Grabando...', 'bg-red-500', true);

    // Iniciar temporizador
    startTime = Date.now();
    timerEl.textContent = '00:00';
    clearInterval(timerInterval);
    timerInterval = setInterval(updateTimer, 1000);
}

/** Estado: procesando */
function setStateProcessing() {
    isProcessing = true;
    recordBtn.disabled = true;
    recordBtn.classList.add('opacity-50', 'cursor-not-allowed');
    btnHint.textContent = 'Procesando...';
    setStatus('Procesando respuesta...', 'bg-amber-500', false);
    // Cambiar icono a spinner
    micIcon.classList.remove('fa-stop');
    micIcon.classList.add('fa-circle-notch', 'fa-spin');
    recordingWave.classList.add('hidden');
    timerContainer.classList.add('hidden');
    clearInterval(timerInterval);
}

/** Estado: reproduciendo */
function setStatePlaying() {
    isProcessing = false;
    recordBtn.disabled = false;
    recordBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    btnHint.textContent = 'Escuchando la respuesta...';
    micIcon.classList.remove('fa-circle-notch', 'fa-spin');
    micIcon.classList.add('fa-microphone');
    setStatus('Reproduciendo respuesta', 'bg-green-500', false);
}

// ============================================================================
// EVENTOS DEL BOTÓN (PUSH-TO-TALK)
// ============================================================================

// Mantener presionado para grabar (desktop)
recordBtn.addEventListener('mousedown', (e) => {
    e.preventDefault();
    if (!isRecording && !isProcessing) startRecording();
});

recordBtn.addEventListener('mouseup', (e) => {
    e.preventDefault();
    if (isRecording) stopRecording();
});

recordBtn.addEventListener('mouseleave', (e) => {
    if (isRecording) stopRecording();
});

// Soporte táctil (mobile)
recordBtn.addEventListener('touchstart', (e) => {
    e.preventDefault();
    if (!isRecording && !isProcessing) startRecording();
}, { passive: false });

recordBtn.addEventListener('touchend', (e) => {
    e.preventDefault();
    if (isRecording) stopRecording();
}, { passive: false });

recordBtn.addEventListener('touchcancel', (e) => {
    e.preventDefault();
    if (isRecording) stopRecording();
}, { passive: false });

// Prevenir contexto de menú al mantener presionado en mobile
recordBtn.addEventListener('contextmenu', (e) => e.preventDefault());

// ============================================================================
// LIMPIEZA AL CERRAR/CAMBIAR DE PÁGINA
// ============================================================================
window.addEventListener('beforeunload', () => {
    if (mediaRecorder && isRecording) {
        mediaRecorder.stop();
    }
});

// Bandera para verificar si el usuario ya interactuó con la página
let hasUserInteracted = false;

// Limpiar al recargar
window.addEventListener('load', () => {
    conversationHistory = [];  // Nuevo chat: limpiar memoria
    setStateIdle();

    // Saludo automático: el bot habla 1-2 segundos después de cargar la página.
    // Nota: los navegadores bloquean audio autoplay sin interacción del usuario.
    // Si el usuario entra directo sin hacer clic, el play fallará y se intentará
    // de nuevo en el primer clic/tecla del usuario (el saludo pendiente se escucha).
    setTimeout(playAutoGreeting, 1500);
});

// Detectar la primera interacción del usuario para habilitar el audio
function markUserInteraction() {
    hasUserInteracted = true;
    // Si hay un saludo pendiente de reproducir, lo lanza ahora
    if (pendingGreetingPromise) {
        const run = pendingGreetingPromise;
        pendingGreetingPromise = null;
        run();
    }
    // Quitar los listeners después de la primera interacción
    window.removeEventListener('click', markUserInteraction);
    window.removeEventListener('keydown', markUserInteraction);
    window.removeEventListener('touchstart', markUserInteraction);
}
window.addEventListener('click', markUserInteraction);
window.addEventListener('keydown', markUserInteraction);
window.addEventListener('touchstart', markUserInteraction);

// Referencia al saludo pendiente (por si el autoplay fue bloqueado)
let pendingGreetingPromise = null;

/**
 * Obtiene el saludo de bienvenida del backend y lo reproduce
 * automáticamente sin que el usuario haga clic.
 */
async function playAutoGreeting() {
    try {
        setStatus('Bienvenido...', 'bg-indigo-500', false);
        const response = await fetch('/api/init-greeting');
        if (!response.ok) {
            console.error('Error obteniendo saludo:', response.status);
            setStateIdle();
            return;
        }

        const greetingBlob = await response.blob();
        const greetingUrl = URL.createObjectURL(greetingBlob);

        // Mostrar el reproductor con el saludo
        audioPlayer.src = greetingUrl;
        playerContainer.classList.remove('hidden');
        setStatePlaying();

        // Detectar fin del saludo
        audioPlayer.onended = () => setStateIdle();

        // Intentar reproducir automáticamente.
        // Si el navegador bloquea el autoplay (sin interacción previa),
        // guardamos la función para ejecutarla en el primer clic del usuario.
        try {
            await audioPlayer.play();
        } catch (autoplayError) {
            console.warn('Autoplay bloqueado por el navegador. Se reproducirá al primer clic.');
            pendingGreetingPromise = () => {
                audioPlayer.play().catch(() => {
                    setStateIdle();
                });
            };
            // Mostrar mensaje de esperar interacción
            btnHint.textContent = 'Haz clic para escuchar el saludo';
            setStatus('Haz clic en cualquier parte para escuchar', 'bg-indigo-500', false);
        }
    } catch (error) {
        console.error('Error reproduciendo saludo:', error);
        setStateIdle();
    }
}
