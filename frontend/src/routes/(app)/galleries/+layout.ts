import { api } from '$lib/api';
import { icons } from '$lib/icons';
import type { Gallery, Menu, Resp } from '$lib/types';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = async () => {
  const menus: Menu[] = [];
  await api
    .get('gallery/lib/list')
    .json<Resp<Gallery[]>>()
    .then(({ data }) => {
      if (data.length > 0) {
        menus.push({
          title: 'nav.galleries.title',
          routes: data.map((gallery) => ({
            title: gallery.name,
            path: `/galleries/${gallery.id}`,
            icon: icons.imageMultiple,
            translate: false
          }))
        });
      }
    });
  return { menus };
};
