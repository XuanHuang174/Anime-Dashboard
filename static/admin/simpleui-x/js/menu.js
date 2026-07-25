/*
 * Project-level SimpleUI menu override.
 *
 * ElementUI renders submenus as right-side popup layers whenever el-menu is
 * collapsed.  That popup covers the admin content area.  Keep the desktop
 * menu expanded so nested Django models remain in the sidebar's normal flow
 * and use ElementUI's built-in accordion transition instead.
 */
Vue.component('sub-menu', {
    props: ['menus'],
    methods: {
        openTab(data) {
            window.app.openTab(data);
        }
    },
    template: `
        <div class="simpleui-accordion-menu">
            <template v-for="item in menus" :key="item.eid">
                <el-menu-item
                    v-if="!item.models"
                    :index="item.eid"
                    @click="openTab(item, item.eid)">
                    <i :class="'menu-icon ' + item.icon"></i>
                    <span>{{ item.name }}</span>
                </el-menu-item>
                <el-submenu v-else :index="item.eid" popper-class="simpleui-inline-submenu">
                    <template slot="title">
                        <i :class="'menu-icon ' + item.icon"></i>
                        <span>{{ item.name }}</span>
                    </template>
                    <sub-menu :menus="item.models"></sub-menu>
                </el-submenu>
            </template>
        </div>
    `
});

Vue.component('multiple-menu', {
    props: ['menus', 'menuActive'],
    mounted() {
        this.$nextTick(() => {
            if (!window.app) return;

            window.app.fold = false;
            window.app.small = false;
            window.app.menuTextShow = true;
            setCookie('fold', false);

            // Keep the mobile drawer behavior, but do not allow the desktop
            // button to switch ElementUI back to popup-submenu mode.
            window.app.foldClick = function () {
                const width = document.documentElement.clientWidth || document.body.clientWidth;
                if (width < 800) {
                    this.drawer = true;
                    return;
                }
                this.fold = false;
                this.small = false;
                this.menuTextShow = true;
                setCookie('fold', false);
            };
        });
    },
    template: `
        <el-menu
            :unique-opened="true"
            :default-active="menuActive"
            :collapse="false"
            :collapse-transition="true">
            <sub-menu :menus="menus"></sub-menu>
        </el-menu>
    `
});
