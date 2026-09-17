// Mobile Menu
document.addEventListener('DOMContentLoaded', function() {
    const mobileMenuBtn = document.getElementById('hamburger');
    const navMenu = document.querySelector('nav ul');
    if (!mobileMenuBtn || !navMenu) return;

    function closeMenu() {
        mobileMenuBtn.setAttribute('aria-expanded', 'false');
        mobileMenuBtn.classList.remove('active');
        navMenu.classList.remove('show');
    }

    function openMenu() {
        mobileMenuBtn.setAttribute('aria-expanded', 'true');
        mobileMenuBtn.classList.add('active');
        navMenu.classList.add('show');
    }

    mobileMenuBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        if (navMenu.classList.contains('show')) closeMenu(); else openMenu();
    });

    // close menu when click link or colour button
    document.querySelectorAll('nav ul li a').forEach(link => {
        link.addEventListener('click', closeMenu);
    });
    document.querySelectorAll('nav ul li button').forEach(button => button.addEventListener('click', closeMenu));

    // close menu if click outside
    document.addEventListener('click', function(event) {
        if (!navMenu.contains(event.target) && !mobileMenuBtn.contains(event.target)) closeMenu();
    });

    window.addEventListener('resize', function() {
        if (window.innerWidth > 1000) { //WIDTH SHOULD MATCH RULE
            closeMenu();
        }
    });
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && navMenu.classList.contains('show')) {
            closeMenu();
            mobileMenuBtn.focus();
        }
    });
});
