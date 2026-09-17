document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('.resume-editor-form');
    if (!form) return;

    const structureInput = form.querySelector('[name="structure"]');
    const status = form.querySelector('.resume-editor-live-status');
    const minimums = { experience: 2, projects: 1, leadership: 1 };
    const titles = { experience: 'Experience', projects: 'Projects', leadership: 'Leadership & activities' };
    const entries = (category) => [...form.querySelectorAll(`[data-entry-id][data-category="${category}"]`)];
    const bullets = (card) => [...card.querySelectorAll('[data-bullet-id]')];

    function announce(message) {
        status.textContent = message;
    }

    function nextId(prefix) {
        let number = 1;
        while (form.querySelector(`[name="${prefix}${number}"]`) ||
               form.querySelector(`[data-entry-id="${prefix}${number}"]`) ||
               form.querySelector(`[data-bullet-id="${prefix}${number}"]`)) number += 1;
        return `${prefix}${number}`;
    }

    function updateLabels(category) {
        entries(category).forEach((card, index) => {
            card.querySelector('h2').textContent = `${titles[category]} ${index + 1}`;
            bullets(card).forEach((field, bulletIndex) => {
                field.querySelector('label').textContent = `Bullet ${bulletIndex + 1}`;
            });
        });
    }

    function updateStructure() {
        const structure = {};
        Object.keys(minimums).forEach((category) => {
            structure[category] = entries(category).map((card) => ({
                id: card.dataset.entryId,
                bullets: bullets(card).map((field) => field.dataset.bulletId),
            }));
            updateLabels(category);
        });
        structureInput.value = JSON.stringify(structure);
    }

    function clearField(field, name) {
        field.removeAttribute('aria-invalid');
        field.removeAttribute('aria-describedby');
        field.name = name;
        field.id = name;
        field.value = '';
        const wrapper = field.closest('.resume-editor-field');
        wrapper.querySelector('label').htmlFor = name;
        wrapper.querySelector('.resume-editor-field-error')?.remove();
    }

    form.addEventListener('click', (event) => {
        const addEntry = event.target.closest('[data-add-entry]');
        const removeEntry = event.target.closest('[data-remove-entry]');
        const addBullet = event.target.closest('[data-add-bullet]');
        const removeBullet = event.target.closest('[data-remove-bullet]');
        if (addEntry) {
            const category = addEntry.dataset.addEntry;
            const cards = entries(category);
            if (cards.length >= 20) return announce('You can keep up to 20 entries in this section.');
            const card = cards[0].cloneNode(true);
            const id = nextId(`${category}_new_`);
            card.dataset.entryId = id;
            if (card.querySelector('[data-entry-section]')) card.querySelector('[data-entry-section]').value = category;
            card.querySelector('h2').id = `${id}_heading`;
            card.setAttribute('aria-labelledby', `${id}_heading`);
            card.querySelectorAll('.resume-editor-field-error').forEach((error) => error.remove());
            bullets(card).slice(1).forEach((field) => field.remove());
            card.querySelectorAll('.resume-editor-field').forEach((wrapper) => {
                const field = wrapper.querySelector('input, textarea');
                const sourceId = cards[0].dataset.entryId;
                const suffix = wrapper.dataset.bulletId ? '_bullet_new_1' : field.name.slice(sourceId.length);
                const name = `${id}${suffix.startsWith('_') ? '' : '_'}${suffix}`;
                clearField(field, name);
                if (wrapper.dataset.bulletId) wrapper.dataset.bulletId = name;
            });
            cards[cards.length - 1].after(card);
            updateStructure();
            announce('Entry added. Fill in its fields before saving.');
            card.querySelector('input')?.focus();
        } else if (removeEntry) {
            const card = removeEntry.closest('[data-entry-id]');
            const category = card.dataset.category;
            if (entries(category).length <= minimums[category]) {
                return announce(`Keep at least ${minimums[category]} ${category} ${minimums[category] === 1 ? 'entry' : 'entries'} for tailoring.`);
            }
            card.remove();
            updateStructure();
            announce('Entry removed. Save the resume to keep this change.');
        } else if (addBullet) {
            const card = addBullet.closest('[data-entry-id]');
            const current = bullets(card);
            if (current.length >= 20) return announce('You can keep up to 20 points per entry.');
            const wrapper = current[0].cloneNode(true);
            const name = nextId(`${card.dataset.entryId}_bullet_new_`);
            wrapper.dataset.bulletId = name;
            clearField(wrapper.querySelector('textarea'), name);
            current[current.length - 1].after(wrapper);
            updateStructure();
            announce('Point added. Fill it in before saving.');
            wrapper.querySelector('textarea').focus();
        } else if (removeBullet) {
            const wrapper = removeBullet.closest('[data-bullet-id]');
            const card = wrapper.closest('[data-entry-id]');
            if (bullets(card).length <= 1) return announce('Keep at least one point in each entry.');
            wrapper.remove();
            updateStructure();
            announce('Point removed. Save the resume to keep this change.');
        }
    });

    form.addEventListener('change', (event) => {
        if (!event.target.matches('[data-entry-section]')) return;
        const card = event.target.closest('[data-entry-id]');
        const destination = event.target.value;
        const source = card.dataset.category;
        if (source === destination) return;
        if (entries(source).length <= minimums[source]) {
            event.target.value = source;
            return announce(`Keep at least ${minimums[source]} entries in ${titles[source]}.`);
        }
        if (entries(destination).length >= 20) {
            event.target.value = source;
            return announce(`You can keep up to 20 entries in ${titles[destination]}.`);
        }
        card.dataset.category = destination;
        const destinationCards = entries(destination).filter((entry) => entry !== card);
        destinationCards[destinationCards.length - 1].after(card);
        updateStructure();
        announce(`Moved entry to ${titles[destination]}. Save the resume to keep this change.`);
    });

    form.addEventListener('submit', updateStructure);
    updateStructure();
});
