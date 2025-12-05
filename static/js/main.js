const menuBtn = document.getElementById('menu-btn');
const mobileMenu = document.getElementById('mobile-menu');
menuBtn?.addEventListener('click', () => {
    mobileMenu.classList.toggle('hidden');
});

// Elements
const askBtn = document.getElementById('askBtn');
const chatModal = document.getElementById('chatModal');
const chatPanel = document.getElementById('chatPanel');
const chatClose = document.getElementById('chatClose');
const chatInput = document.getElementById('chatInput');
const chatForm = document.getElementById('chatForm');
const chatMessages = document.getElementById('chatMessages');

// Open/close helpers
function openChat() {
chatModal.classList.remove('hidden');
setTimeout(() => chatInput.focus(), 10);
}
function closeChat() {
chatModal.classList.add('hidden');
}

// Open / close wiring
askBtn.addEventListener('click', (e) => {
e.stopPropagation();
openChat();
});
chatClose.addEventListener('click', closeChat);

document.addEventListener('mousedown', (e) => {
if (chatModal.classList.contains('hidden')) return;
if (!chatPanel.contains(e.target) && e.target !== askBtn) {
    closeChat();
}
});

document.addEventListener('keydown', (e) => {
if (!chatModal.classList.contains('hidden') && e.key === 'Escape') closeChat();
});

// Auto-grow textarea
chatInput.addEventListener('input', () => {
chatInput.style.height = 'auto';
chatInput.style.height = Math.min(chatInput.scrollHeight, 160) + 'px';
});

// Chat submit + backend + markdown + math
chatForm.addEventListener('submit', async (e) => {
e.preventDefault();
const text = chatInput.value.trim();
if (!text) return;

// 1. User message
chatMessages.insertAdjacentHTML('beforeend', `
    <div class="flex gap-3 justify-end items-start">
    <div class="rounded-2xl rounded-tr-sm bg-[#b21f17] text-white px-4 py-2 max-w-[80%]">
        ${escapeHtml(text)}
    </div>
    <div class="h-8 w-8 shrink-0 grid place-items-center rounded-full bg-gray-200 text-gray-700 font-semibold">
        You
    </div>
    </div>
`);
chatMessages.scrollTop = chatMessages.scrollHeight;
chatInput.value = '';
chatInput.style.height = 'auto';

// 2. Typing indicator
const typingId = 'assistant-typing';
chatMessages.insertAdjacentHTML('beforeend', `
    <div id="${typingId}" class="flex gap-3 mt-2">
    <div class="h-8 w-8 shrink-0 grid place-items-center rounded-full bg-[#b21f17] text-white font-semibold">A</div>
    <div class="rounded-2xl rounded-tl-sm bg-gray-100 px-4 py-2 max-w-[80%]">
        <span class="text-sm text-gray-500 animate-pulse">Thinking...</span>
    </div>
    </div>
`);
chatMessages.scrollTop = chatMessages.scrollHeight;

try {
    // 3. Call backend (send both keys for compatibility)
    const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        message: text,
        messages: text
    })
    });

    if (!res.ok) {
    const errText = await res.text();
    console.error('Server error body:', errText);
    throw new Error('Server error: ' + res.status);
    }

    const data = await res.json();
    const replyRaw =
    typeof data.output === 'string' ? data.output :
    typeof data.reply === 'string' ? data.reply :
    JSON.stringify(data);

    const replyText = replyRaw.trim();

    // Remove typing
    const typingEl = document.getElementById(typingId);
    if (typingEl) typingEl.remove();

    // If we somehow got an empty reply, don't render a blank bubble
    if (!replyText) {
    console.warn('Empty reply from backend, skipping render:', data);
    return;
    }

    // --- Markdown + Math: protect LaTeX, then run marked, then restore ---
    const { replaced, math } = extractMathSegments(replyText);


    marked.setOptions({
    breaks: true,
    mangle: false,
    headerIds: false
    });

    const htmlWithPlaceholders = marked.parse(replaced);
    const replyHtml = restoreMathSegments(htmlWithPlaceholders, math);

    // 4. Assistant message (markdown + math, scroll if long)
    chatMessages.insertAdjacentHTML('beforeend', `
    <div class="flex gap-3">
        <div class="h-8 w-8 shrink-0 grid place-items-center rounded-full bg-[#b21f17] text-white font-semibold">A</div>
        <div class="rounded-2xl rounded-tl-sm bg-gray-100 px-4 py-2 max-w-[80%] overflow-x-auto">
        <div class="assistant-message break-words">
            ${replyHtml}
        </div>
        </div>
    </div>
    `);

    chatMessages.scrollTop = chatMessages.scrollHeight;

    // 5. MathJax for last assistant message (wait for startup)
    if (window.MathJax) {
    const mj = window.MathJax;
    const msgs = chatMessages.querySelectorAll('.assistant-message');
    const lastMsg = msgs[msgs.length - 1];

    if (lastMsg) {
        (mj.startup?.promise || Promise.resolve())
        .then(() => mj.typesetPromise([lastMsg]))
        .catch(err => console.error('MathJax error:', err));
    }
    }



} catch (err) {
    console.error('Error talking to backend:', err);

    const typingEl = document.getElementById(typingId);
    if (typingEl) typingEl.remove();

    chatMessages.insertAdjacentHTML('beforeend', `
    <div class="flex gap-3">
        <div class="rounded-2xl bg-red-100 px-4 py-2 text-red-600 border border-red-200 text-sm">
        ⚠️ <strong>Connection Error:</strong> Is your Python server running?<br>
        Check your terminal for errors.
        </div>
    </div>
    `);
}

chatMessages.scrollTop = chatMessages.scrollHeight;
});

// Escape for user text only (does NOT touch backslashes, so LaTeX is safe)
// Escape for user text only (does NOT touch backslashes, so LaTeX is safe)
function escapeHtml(str) {
    return str.replace(/[&<>"']/g, m => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
    }[m]));
}

// Extract LaTeX segments so marked doesn't mangle them
function extractMathSegments(text) {
    const math = [];
    const patterns = [
    /\$\$[\s\S]*?\$\$/g,        // $$ ... $$
    /\\\[[\s\S]*?\\\]/g,        // \[ ... \]
    /\$[^$\n]+\$/g,             // $ ... $
    /\\\([^\\]*?\\\)/g          // \( ... \)
    ];
    let replaced = text;

    patterns.forEach((re) => {
    replaced = replaced.replace(re, (m) => {
        const id = math.length;
        math.push(m);
        return `@@MATH${id}@@`;
    });
    });

    return { replaced, math };
}

// Put LaTeX back into the HTML after markdown rendering
function restoreMathSegments(html, math) {
    return html.replace(/@@MATH(\d+)@@/g, (_, i) => math[Number(i)] || '');
}

document.addEventListener('DOMContentLoaded', async () => {
try {
const res = await fetch('/chat_history');
if (!res.ok) return;

const data = await res.json();
const history = data.history || [];

if (!history.length) return;

const chatMessages = document.getElementById('chatMessages');
chatMessages.innerHTML = ''; // clear default greeting

history.forEach(turn => {
    if (turn.role === 'human') {
    chatMessages.insertAdjacentHTML('beforeend', `
        <div class="flex gap-3 justify-end items-start">
        <div class="rounded-2xl rounded-tr-sm bg-[#b21f17] text-white px-4 py-2 max-w-[80%]">
            ${escapeHtml(turn.content)}
        </div>
        <div class="h-8 w-8 shrink-0 grid place-items-center rounded-full bg-gray-200 text-gray-700 font-semibold">
            You
        </div>
        </div>
    `);
    }
    else if (turn.role === 'ai') {
    // assistant bubble (reuse same markdown + math logic you have)
    const replyText = turn.content.trim();
    const { replaced, math } = extractMathSegments(replyText);
    const htmlWithPlaceholders = marked.parse(replaced);
    const replyHtml = restoreMathSegments(htmlWithPlaceholders, math);
    if (!replyHtml || replyHtml.length === 0) return;

    chatMessages.insertAdjacentHTML('beforeend', `
        <div class="flex gap-3">
        <div class="h-8 w-8 shrink-0 grid place-items-center rounded-full bg-[#b21f17] text-white font-semibold">A</div>
        <div class="rounded-2xl rounded-tl-sm bg-gray-100 px-4 py-2 max-w-[80%] overflow-x-auto">
            <div class="assistant-message break-words">
            ${replyHtml}
            </div>
        </div>
        </div>
    `);
    }
});

// typeset math for all assistant messages
if (window.MathJax) {
    const mj = window.MathJax;
    const msgs = chatMessages.querySelectorAll('.assistant-message');
    (mj.startup?.promise || Promise.resolve())
    .then(() => mj.typesetPromise(Array.from(msgs)))
    .catch(console.error);
}

chatMessages.scrollTop = chatMessages.scrollHeight;
} catch (err) {
console.error('Failed to load chat history:', err);
}
});


const chatClear = document.getElementById('chatClear');

chatClear.addEventListener('click', async () => {
try {
const res = await fetch('/clear_history', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
});

if (!res.ok) {
    console.error('Failed to clear history:', await res.text());
    return;
}

// Clear UI after server confirms
chatMessages.innerHTML = `
    <div class="flex gap-3">
    <div class="h-8 w-8 shrink-0 grid place-items-center rounded-full bg-[#b21f17] text-white font-semibold">A</div>
    <div class="rounded-2xl rounded-tl-sm bg-gray-100 px-4 py-2 max-w-[95%]">
        Conversation cleared. What would you like to ask next?
    </div>
    </div>
`;
} catch (err) {
    console.error('Error clearing history:', err);
    }
});
