"""Server-authoritative endless platform arena, deterministic chunk generation.

The level grows as players travel. Coins never respawn during a match.
Snapshots include nearby geometry only to keep UDP packets small.
"""
import math
import random

WIDTH, HEIGHT = 10**12, 720  # logical open-ended world; no practical right edge
GROUND_Y = 645
PLAYER_W, PLAYER_H = 34, 46
ACCEL, FRICTION, MAX_SPEED = 1850., 2200., 285.
GRAVITY, JUMP_SPEED, MAX_FALL = 1450., -585., 850.
CHUNK = 850
VIEW_RADIUS = 1250
SEED = 48173


def spawn_player(index, x=None):
    x = float(60 + 60 * index if x is None else x)
    return dict(x=x, y=float(GROUND_Y-PLAYER_H), vx=0., vy=0.,
                grounded=True, jump_held=False, coyote=0., jump_buffer=0.,
                facing=1, invuln=1.2, checkpoint=x, speed_until=0., jump_until=0.)


def create_world(players):
    world = {'players': {name: spawn_player(i) for i, name in enumerate(players)},
             'coins': {}, 'platforms': {}, 'hazards': {}, 'moving_hazards': {}, 'pickups': {},
             'elapsed': 0., 'effects': [], 'generated': set()}
    ensure_chunks(world, 0, 2)
    return world


def ensure_chunks(world, first, last):
    """Create deterministic, traversable sections, only once per match."""
    for index in range(max(0, first), max(0, last) + 1):
        if index in world['generated']:
            continue
        world['generated'].add(index)
        rng = random.Random(SEED + index * 7919)
        base = index * CHUNK
        # A stepped path: vertical differences of 65 px, safely below jump height.
        shift = rng.randint(-18, 18)
        platforms = [
            (base + 95, 565, 180, 22),
            (base + 290, 500, 180, 22),
            (base + 480, 435, 175, 22),
            (base + 610, 370, 145, 22),
        ]
        platforms = [(x + shift, y, w, h) for x, y, w, h in platforms]
        world['platforms'][index] = platforms
        # Hazards only on the ground, after the starting safe zone.
        world['hazards'][index] = [] if index == 0 else [
            (base + 760, GROUND_Y - 20, 46, 20)]
        # Moving saws: deterministic motion based on authoritative match time.
        # Their positions are computed on the server and transmitted in snapshots.
        world['moving_hazards'][index] = [] if index == 0 else [
            dict(id=index, x=base+345, y=GROUND_Y-31, w=30, h=30,
                 amplitude=78, frequency=1.65, phase=index*1.7)]
        coins = []
        for j, (x, y, w, _) in enumerate(platforms):
            coins.append(dict(id=index * 10 + j, x=x + w/2, y=y-28, active=True))
        for j, dx in enumerate((55, 390, 700), 4):
            coins.append(dict(id=index * 10 + j, x=base + dx, y=GROUND_Y-37, active=True))
        world['coins'][index] = coins
        # One deterministic rare pickup per chunk. No respawns in a match.
        # Chunk zero gives players a chance to discover the new mechanic.
        kind = ('star', 'speed', 'gem', 'jump')[index % 4]
        px, py, pw, _ = platforms[index % len(platforms)]
        world['pickups'][index] = [dict(id=index, kind=kind,
                                        x=px+pw/2, y=py-65, active=True)]
        # Golden stars get a dedicated platform: remove the ordinary coin
        # directly beneath them so the two collectibles never appear stacked.
        if kind == 'star':
            world['coins'][index] = [c for c in world['coins'][index]
                                     if c['id'] != index * 10 + (index % len(platforms))]


def moving_hazard_rect(h, elapsed):
    # Horizontal oscillation; never modifies the static world geometry.
    return (h['x'] + math.sin(elapsed*h['frequency']+h['phase'])*h['amplitude'],
            h['y'], h['w'], h['h'])


def nearby_hazards(world, chunk):
    for ci in range(max(0,chunk-1),chunk+2):
        yield from world['hazards'].get(ci, ())
        for h in world['moving_hazards'].get(ci, ()):
            yield moving_hazard_rect(h, world['elapsed'])


def overlap(ax, ay, aw, ah, bx, by, bw, bh):
    return ax < bx+bw and ax+aw > bx and ay < by+bh and ay+ah > by


def step(world, inputs, dt, score_callback):
    dt = min(max(float(dt), 0.), .05)
    world['elapsed'] += dt
    world['effects'] = [e for e in world['effects'] if e['until'] > world['elapsed']]
    for name, p in world['players'].items():
        ctl = inputs.get(name, {})
        direction = int(bool(ctl.get('right'))) - int(bool(ctl.get('left')))
        speed_cap = MAX_SPEED * (1.55 if p['speed_until'] > world['elapsed'] else 1.)
        if direction:
            p['vx'] = max(-speed_cap, min(speed_cap, p['vx'] + direction*ACCEL*dt))
            p['facing'] = direction
        else:
            delta = FRICTION * dt
            p['vx'] = max(0., p['vx']-delta) if p['vx'] > 0 else min(0., p['vx']+delta)
        jump = bool(ctl.get('jump'))
        if jump and not p['jump_held']:
            p['jump_buffer'] = .12
        p['jump_held'] = jump
        p['jump_buffer'] = max(0., p['jump_buffer']-dt)
        p['coyote'] = .09 if p['grounded'] else max(0., p['coyote']-dt)
        if p['jump_buffer'] > 0 and p['coyote'] > 0:
            p['vy'] = JUMP_SPEED * (1.35 if p['jump_until'] > world['elapsed'] else 1.)
            p['grounded'] = False
            p['coyote'] = p['jump_buffer'] = 0.
        if not jump and p['vy'] < -130:
            p['vy'] += GRAVITY * 1.15 * dt
        p['x'] = max(0., p['x'] + p['vx']*dt)
        chunk = int(p['x']//CHUNK)
        ensure_chunks(world, chunk-1, chunk+2)
        previous_bottom = p['y']+PLAYER_H
        p['vy'] = min(MAX_FALL, p['vy'] + GRAVITY*dt)
        p['y'] += p['vy']*dt
        p['grounded'] = False
        if p['vy'] >= 0:
            surfaces = [(0, GROUND_Y, WIDTH, HEIGHT-GROUND_Y)]
            for ci in range(max(0, chunk-1), chunk+2):
                surfaces.extend(world['platforms'].get(ci, ()))
            for sx, sy, sw, _ in surfaces:
                if (previous_bottom <= sy+5 and p['y']+PLAYER_H >= sy
                        and p['x']+PLAYER_W > sx+4 and p['x'] < sx+sw-4):
                    p['y'] = sy-PLAYER_H
                    p['vy'] = 0.
                    p['grounded'] = True
                    break
        p['invuln'] = max(0., p['invuln']-dt)
        if p['grounded'] and p['x'] > p['checkpoint']+350:
            p['checkpoint'] = p['x']
        if p['invuln'] == 0 and any(
                overlap(p['x']+6, p['y']+20, PLAYER_W-12, PLAYER_H-20, *h)
                for h in nearby_hazards(world, chunk)):
            # Respawn near the last safe progress, rather than the start.
            old = spawn_player(0, max(30., p['checkpoint']-90.))
            p.update(old)
            p['invuln'] = 1.5
            world['effects'].append(dict(type='ouch', x=p['x'], y=p['y'],
                                         until=world['elapsed']+.65))
        for ci in range(max(0,chunk-1),chunk+2):
            for item in world['pickups'].get(ci, ()):
                if not item['active'] or not overlap(p['x'], p['y'], PLAYER_W, PLAYER_H,
                                                     item['x']-15, item['y']-15, 30, 30):
                    continue
                item['active'] = False
                kind = item['kind']
                points = {'star':50, 'gem':25}.get(kind, 0)
                if points:
                    score_callback(name, points)
                elif kind == 'speed':
                    p['speed_until'] = world['elapsed'] + 7.
                elif kind == 'jump':
                    p['jump_until'] = world['elapsed'] + 9.
                world['effects'].append(dict(type='pickup', kind=kind, name=name,
                                             points=points, x=item['x'], y=item['y'],
                                             until=world['elapsed']+1.2))
        for ci in range(max(0,chunk-1),chunk+2):
            for coin in world['coins'].get(ci, ()):
                if coin['active'] and overlap(p['x'],p['y'],PLAYER_W,PLAYER_H,
                                              coin['x']-11,coin['y']-11,22,22):
                    coin['active'] = False
                    score_callback(name,10)
                    world['effects'].append(dict(type='coin',x=coin['x'],y=coin['y'],
                                                 until=world['elapsed']+.55))


def snapshot(world):
    # Send only the sections around players. Keeps snapshots small even if the
    # match travels very far. Each client sees the same authoritative state.
    nearby = set()
    for p in world['players'].values():
        ci = int(p['x']//CHUNK)
        nearby.update(range(max(0,ci-2),ci+3))
    platforms = [p for ci in sorted(nearby) for p in world['platforms'].get(ci,())]
    hazards = [h for ci in sorted(nearby) for h in world['hazards'].get(ci,())]
    moving = [dict(id=h['id'], x=round(moving_hazard_rect(h,world['elapsed'])[0],2),
                   y=h['y'], w=h['w'], h=h['h'])
              for ci in sorted(nearby) for h in world['moving_hazards'].get(ci,())]
    coins = [{'id':c['id'],'x':c['x'],'y':c['y']}
             for ci in sorted(nearby) for c in world['coins'].get(ci,()) if c['active']]
    pickups = [{'id':item['id'], 'kind':item['kind'], 'x':item['x'], 'y':item['y']}
               for ci in sorted(nearby) for item in world['pickups'].get(ci, ()) if item['active']]
    return {'players':{name:{k:round(p[k],2) for k in ('x','y','vx','vy','facing','invuln','speed_until','jump_until')}
                       for name,p in world['players'].items()},
            'coins':coins,'pickups':pickups,'effects':list(world['effects']),
            'platforms':platforms,'hazards':hazards,'moving_hazards':moving,
            'width':WIDTH,'height':HEIGHT,'ground_y':GROUND_Y,
            'elapsed':round(world['elapsed'],2)}
