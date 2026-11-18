"""Super Platform Arena: original procedurally-drawn multiplayer Pygame client."""
import json, socket, threading, time, queue, math, random, getpass, os
try:
    import pygame
except ImportError:
    raise SystemExit('Pygame missing. Run: python -m pip install pygame')

# The client starts with a graphical login screen. No terminal prompts required.
HOST='127.0.0.1'; USERNAME=''; PASSWORD=''; ROOM='Arena1'
messages=queue.Queue(); send_lock=threading.Lock()
tcp=None; udp=None; server_udp=None
connected=False; authenticated=False; udp_ok=False
connect_generation=0

def send_tcp(obj):
    if tcp is None: return
    try:
        with send_lock: tcp.sendall((json.dumps(obj)+'\n').encode())
    except OSError: messages.put(('tcp',{'type':'connection_closed'}))

def tcp_reader(sock, generation):
    try:
        with sock.makefile('r',encoding='utf-8') as stream:
            for line in stream:
                try:
                    packet=json.loads(line)
                    messages.put(('tcp',packet,generation))
                except ValueError: pass
    except OSError: pass
    messages.put(('tcp',{'type':'connection_closed'},generation))

def begin_connect(host, username, password, register=False):
    global HOST,USERNAME,PASSWORD,connect_generation,tcp,connected
    HOST=host.strip() or '127.0.0.1'
    USERNAME=username.strip();PASSWORD=password
    if not USERNAME or not PASSWORD:
        set_notice('Please enter a username and password.');return
    if tcp is not None and not authenticated:
        try:tcp.close()
        except OSError:pass
    connect_generation+=1
    generation=connect_generation
    set_notice('Connecting to '+HOST+' ...')
    def worker():
        try:
            sock=socket.create_connection((HOST,5000),timeout=6)
            sock.settimeout(None)
            messages.put(('local',{'type':'connected','socket':sock,'register':register},generation))
        except OSError as exc:
            messages.put(('local',{'type':'connect_failed','message':str(exc)},generation))
    threading.Thread(target=worker,daemon=True).start()

def start_udp(login):
    global udp,server_udp,last_hello,udp_ok
    udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    udp.settimeout(.4)
    server_udp=(HOST,login['udp_port'])
    def reader(sock):
        while True:
            try:
                raw,_=sock.recvfrom(32768)
                msg=json.loads(raw)
                if msg.get('type') in ('game_state','udp_welcome'):
                    messages.put(('udp',msg,connect_generation))
            except socket.timeout:continue
            except (OSError,ValueError):return
    threading.Thread(target=reader,args=(udp,),daemon=True).start()
    udp_ok=False;last_hello=0
    hello()

def hello():
    if udp is None or server_udp is None or not udp_token:return
    try:udp.sendto(json.dumps({'type':'udp_hello','token':udp_token}).encode(),server_udp)
    except OSError:pass

udp_token=None
# Optional audio: gameplay still works if the computer has no sound device.
SOUND_ENABLED=True
SOUNDS={}
try:
    if not pygame.mixer.get_init(): pygame.mixer.init(frequency=22050, size=-16, channels=1)
    _sound_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)),'assets','sounds')
    for _name in ('jump','coin','star','gem','speed','power_jump','hit','land','go','victory'):
        try:
            SOUNDS[_name]=pygame.mixer.Sound(os.path.join(_sound_dir,_name+'.wav'))
            SOUNDS[_name].set_volume(.32)
        except (pygame.error,OSError): pass
except (pygame.error,OSError): pass

def play_sound(name):
    if SOUND_ENABLED and name in SOUNDS:
        try: SOUNDS[name].play()
        except pygame.error: pass

# All particles are cosmetic and never modify authoritative server state.
visual_particles=[]
seen_effects={}
last_player_state=None
last_jump_key=False
last_countdown_active=False
particle_rng=random.Random(1172)

def burst(x,y,color,count=16,gravity=85):
    for _ in range(count):
        angle=particle_rng.random()*math.tau
        speed=45+particle_rng.random()*160
        visual_particles.append([x,y,math.cos(angle)*speed,math.sin(angle)*speed,
                                 .45+particle_rng.random()*.42,color,gravity])
    if len(visual_particles)>260: del visual_particles[:-260]

def update_particles(dt):
    for p in visual_particles[:]:
        p[0]+=p[2]*dt; p[1]+=p[3]*dt; p[3]+=p[6]*dt; p[4]-=dt
        if p[4]<=0:visual_particles.remove(p)

def draw_particles(cam):
    for x,y,vx,vy,life,color,gravity in visual_particles:
        sx=int(x-cam)
        if -10<=sx<=SW+10 and -10<=y<=SH+10:
            pygame.draw.circle(screen,color,(sx,int(y)),3 if life>.28 else 2)

def handle_game_effects(snapshot,t):
    global last_player_state
    if not snapshot:return
    for effect in snapshot.get('effects',[]):
        kind=effect.get('type')
        if kind not in ('coin','pickup'):continue
        key=(kind,effect.get('kind'),effect.get('x'),effect.get('y'))
        if key in seen_effects:continue
        seen_effects[key]=t
        x=float(effect.get('x',0)); y=float(effect.get('y',0))
        if kind=='coin':
            burst(x,y,(255,223,92),12);play_sound('coin')
        else:
            item=effect.get('kind')
            color={'star':(255,220,75),'gem':(191,115,255),
                   'speed':(79,228,255),'jump':(134,252,157)}.get(item,(255,255,255))
            burst(x,y,color,26)
            play_sound({'star':'star','gem':'gem','speed':'speed','jump':'power_jump'}.get(item,'coin'))
    for key,created in list(seen_effects.items()):
        if t-created>3:seen_effects.pop(key,None)
    own=snapshot.get('players',{}).get(USERNAME)
    if own:
        if last_player_state is not None:
            old=last_player_state
            # Landing/respawn cues are detected from consecutive server snapshots.
            if old.get('vy',0)>110 and abs(own.get('vy',0))<30 and abs(own.get('y',0)-old.get('y',0))<100:
                burst(own['x']+17,own['y']+46,(221,213,178),9,35)
                play_sound('land')
            if own.get('invuln',0)>old.get('invuln',0)+.12:
                burst(own['x']+17,own['y']+20,(255,115,104),20)
                play_sound('hit')
        last_player_state=dict(own)

pygame.init()
SW,SH=1100,700
screen=pygame.display.set_mode((SW,SH),pygame.RESIZABLE)
pygame.display.set_caption('SUPER PLATFORM ARENA')
clock=pygame.time.Clock()
FONTS={size:pygame.font.SysFont('consolas',size,bold=True) for size in (16,18,20,24,30,42,58)}
PALETTE=[(67,183,242),(255,119,119),(113,224,165),(218,147,255)]
room=None; world=None; scores={}; match_id=None; remaining=None
countdown_remaining=0.0; countdown_last_update=0.0; match_start_local=0.0
notice='Enter your details to connect to the server'; notice_time=time.monotonic()
result=None; seq=0; last_input=0.;last_hello=time.monotonic();udp_ok=False
running=True;camera=0.;display_positions={};particles=[];last_snapshot=-1.;jump_was_down=False
page='login'; rooms_list=[]; buttons=[]; fields={}; active_field=None; pending_register=False
CHARACTERS=('Mario','Luigi','Peach','Toad')
character_images={}
for character in CHARACTERS:
    path=os.path.join(os.path.dirname(os.path.abspath(__file__)),'assets','characters',character.lower()+'.png')
    try:
        image=pygame.image.load(path).convert_alpha()
        h=57; w=max(1,round(image.get_width()*h/image.get_height()))
        character_images[character]=pygame.transform.scale(image,(w,h))
    except (pygame.error,OSError,ZeroDivisionError):pass


# Four original running frames for each character, extracted from the supplied sheet.
run_frames = {}
for character in CHARACTERS:
    frames=[]
    for index in range(4):
        path=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'assets','characters','run',f'{character.lower()}_{index}.png')
        try:
            image=pygame.image.load(path).convert_alpha()
            h=59; w=max(1,round(image.get_width()*h/image.get_height()))
            frames.append(pygame.transform.scale(image,(w,h)))
        except (pygame.error,OSError,ZeroDivisionError):
            frames=[];break
    if len(frames)==4:run_frames[character]=frames


# Deterministic decorative sky coordinates; gameplay remains server-authoritative.
rng=random.Random(88)
clouds=[(rng.randrange(0,2400),rng.randrange(75,300),rng.randrange(35,95)) for _ in range(26)]
stars=[(rng.randrange(0,2400),rng.randrange(25,430),rng.randrange(1,3)) for _ in range(100)]

def text(s,x,y,size=20,color=(255,255,255),center=False,shadow=False):
    font=FONTS.get(size,FONTS[20]);surf=font.render(str(s),True,color)
    pos=surf.get_rect(center=(int(x),int(y))) if center else surf.get_rect(topleft=(int(x),int(y)))
    if shadow:
        sh=font.render(str(s),True,(18,37,64));screen.blit(sh,pos.move(2,3))
    screen.blit(surf,pos)

def rect(color,r,radius=0,border=0):
    pygame.draw.rect(screen,color,pygame.Rect(*map(int,r)),border,border_radius=radius)

def cloud(x,y,w):
    col=(245,252,255)
    pygame.draw.ellipse(screen,col,(int(x),int(y+15),int(w*1.5),int(w*.38)))
    pygame.draw.circle(screen,col,(int(x+w*.42),int(y+10)),int(w*.31))
    pygame.draw.circle(screen,col,(int(x+w*.88),int(y+5)),int(w*.39))

# Visual-only biomes: synchronized to the authoritative server match clock.
BIOME_SECONDS = 30
BIOMES = (
    {'name':'MEADOW','sky':(110,201,249),'hills':(95,174,130),'far':(138,196,168),'ground':(126,83,63),'grass':(91,194,103),'trim':(170,231,115)},
    {'name':'FOREST','sky':(117,184,185),'hills':(48,113,104),'far':(77,148,129),'ground':(102,73,64),'grass':(64,150,88),'trim':(128,207,105)},
    {'name':'SNOWFIELD','sky':(137,198,239),'hills':(130,160,190),'far':(183,207,226),'ground':(103,122,148),'grass':(219,238,250),'trim':(255,255,255)},
    {'name':'NIGHT CASTLE','sky':(31,38,85),'hills':(53,62,102),'far':(71,77,121),'ground':(77,70,91),'grass':(120,105,154),'trim':(168,146,199)},
)

def current_biome_index():
    # The server broadcasts elapsed match seconds in each authoritative snapshot.
    # All clients, including players at different X positions, share this phase.
    if world is None:
        return 0
    return int(max(0.0, float(world.get('elapsed', 0))) // BIOME_SECONDS) % len(BIOMES)

def current_biome():
    return BIOMES[current_biome_index()]

# V9 art renderer: pre-painted PNG scenery, layered parallax and textured tiles.
ART_DIR=os.path.join(os.path.dirname(os.path.abspath(__file__)),'assets','world')
ART={}
for _bi in range(4):
    for _layer in ('sky','mountains','forest','dirt','top'):
        _path=os.path.join(ART_DIR,f'{_bi}_{_layer}.png')
        try:
            _im=pygame.image.load(_path).convert_alpha()
            if _layer in ('sky','mountains','forest'):
                _im=pygame.transform.scale(_im,(1600,700))
            ART[(_bi,_layer)]=_im
        except (pygame.error,OSError):
            pass

def art_biome(x=None):
    # x is accepted for compatibility, but environment no longer depends on X.
    return current_biome_index()

def art_layer(layer,cam,bi,parallax):
    image=ART.get((bi,layer))
    if image is None:return
    offset=int(cam*parallax)%image.get_width()
    x=-offset
    while x<SW:
        screen.blit(image,(x,0))
        x+=image.get_width()

def background(t,cam):
    bi=art_biome(cam+SW*.45)
    if (bi,'sky') not in ART:
        screen.fill(BIOMES[bi]['sky'])
        return
    art_layer('sky',cam,bi,.035)
    art_layer('mountains',cam,bi,.16)
    art_layer('forest',cam,bi,.36)
    # Floating dust, fireflies and snowflakes give the background gentle motion.
    for j in range(36):
        wx=(j*183+int(t*(8 if bi==2 else -3)))%(SW+120)-60
        wy=(j*97+int(math.sin(t*.7+j)*11))%max(1,SH-100)
        if bi==2:
            pygame.draw.circle(screen,(246,251,255),(wx,wy),2 if j%3==0 else 1)
        elif bi==3:
            pygame.draw.circle(screen,(242,206,123),(wx,wy),2 if j%4==0 else 1)
        else:
            pygame.draw.circle(screen,(237,238,189),(wx,wy),1)

def tiled_surface(layer,bi,x,y,w,h):
    tile=ART.get((bi,layer))
    if tile is None:
        pygame.draw.rect(screen,BIOMES[bi]['ground'],(int(x),int(y),int(w),int(h)))
        return
    tw,th=tile.get_size()
    # Clip tile rendering to the platform bounds, preserving physics dimensions.
    region=pygame.Rect(int(x),int(y),max(0,int(w)),max(0,int(h)))
    old_clip=screen.get_clip()
    screen.set_clip(region.clip(screen.get_rect()))
    for xx in range(int(x),int(x+w),tw):
        for yy in range(int(y),int(y+h),th):
            screen.blit(tile,(xx,yy))
    screen.set_clip(old_clip)

def ground(cam,gy):
    gy=int(gy)
    # Seamless terrain texture anchored to the actual world, not the camera.
    bi=art_biome(cam+SW*.45)
    tile=ART.get((bi,'dirt'))
    if tile:
        tw,th=tile.get_size()
        for xx in range(-int(cam)%tw-tw,SW+tw,tw):
            for yy in range(gy,SH+th,th):
                screen.blit(tile,(xx,yy))
    else:
        rect(BIOMES[bi]['ground'],(0,gy,SW,SH-gy))
    top=ART.get((bi,'top'))
    if top:
        for xx in range(-int(cam)%top.get_width()-top.get_width(),SW+top.get_width(),top.get_width()):
            screen.blit(top,(xx,gy))

def platform(x,y,w,h,cam):
    sx=int(x-cam);y=int(y)
    if sx+w<-25 or sx>SW+25:return
    bi=art_biome(x+w*.5)
    # Drop shadow, then detailed pixel tiles.
    pygame.draw.rect(screen,(27,36,45),(sx+5,y+8,int(w),int(h)+10))
    tiled_surface('dirt',bi,sx,y+7,w,h+9)
    tiled_surface('top',bi,sx,y,w,15)
    # Small plants and pebbles that stay attached to the platforms.
    rng=random.Random(int(x)//17)
    for j in range(6,int(w)-5,17):
        if rng.random()<.42:
            dx=sx+j
            pygame.draw.line(screen,BIOMES[bi]['grass'],(dx,y+3),(dx+2,y-5),2)
            pygame.draw.line(screen,BIOMES[bi]['trim'],(dx+2,y-4),(dx+6,y-6),2)

def spikes(x,y,w,h,cam):
    sx=int(x-cam);y=int(y)
    if sx+w<0 or sx>SW:return
    pygame.draw.rect(screen,(50,48,65),(sx,y+14,int(w),7))
    for dx in range(0,int(w)-4,14):
        pygame.draw.polygon(screen,(199,211,220),[(sx+dx,y+16),(sx+dx+7,y-4),(sx+dx+14,y+16)])
        pygame.draw.polygon(screen,(247,250,250),[(sx+dx+7,y-4),(sx+dx+7,y+12),(sx+dx+10,y+12)])

def moving_saw(h,cam,t):
    """Render server-positioned animated saw, not a client-side collision guess."""
    x=int(h['x']-cam+h['w']/2); y=int(h['y']+h['h']/2)
    if x<-50 or x>SW+50:return
    radius=17
    pygame.draw.circle(screen,(37,42,58),(x+3,y+4),radius+4)
    pygame.draw.circle(screen,(244,123,57),(x,y),radius+5)
    for j in range(12):
        a=j*math.tau/12+t*5
        tip=(int(x+math.cos(a)*(radius+9)),int(y+math.sin(a)*(radius+9)))
        left=(int(x+math.cos(a-.19)*radius),int(y+math.sin(a-.19)*radius))
        right=(int(x+math.cos(a+.19)*radius),int(y+math.sin(a+.19)*radius))
        pygame.draw.polygon(screen,(214,225,237),(left,tip,right))
    pygame.draw.circle(screen,(204,220,235),(x,y),radius)
    pygame.draw.circle(screen,(93,113,136),(x,y),radius-5,3)
    pygame.draw.circle(screen,(51,65,85),(x,y),5)
    pygame.draw.circle(screen,(255,235,152),(x-2,y-2),2)


def biome_transition(t):
    """A shared, timed fade around server-defined environment boundaries."""
    if not world:return
    elapsed=float(world.get('elapsed',0))
    phase=elapsed%BIOME_SECONDS
    distance=min(phase,BIOME_SECONDS-phase)
    if distance>1.2:return
    # Fade to dark at the shared boundary, then reveal the next environment.
    alpha=int(185*(1-distance/1.2))
    veil=pygame.Surface((SW,SH),pygame.SRCALPHA)
    veil.fill((12,20,37,alpha))
    screen.blit(veil,(0,0))
    # Quiet transition: no announcement or text over gameplay.


def coin(x,y,t,cam):
    sx=int(x-cam)
    if sx<-20 or sx>SW+20:return
    sy=int(y+math.sin(t*5+x*.015)*4)
    ww=max(4,int(12*abs(math.cos(t*3+x*.025))))
    pygame.draw.ellipse(screen,(128,79,31),(sx-ww+2,sy-11,ww*2+2,24))
    pygame.draw.ellipse(screen,(255,200,55),(sx-ww,sy-13,ww*2,24))
    pygame.draw.ellipse(screen,(255,246,159),(sx-ww+2,sy-10,max(2,ww*2-4),18),2)
    if ww>7:
        pygame.draw.line(screen,(255,252,191),(sx-2,sy-7),(sx-2,sy+6),2)

def draw_pickup(item, cam, t):
    x=int(item['x']-cam); y=int(item['y']+math.sin(t*4+item['x']*.01)*5)
    if x < -35 or x > SW+35: return
    kind=item['kind']
    colors={'star':(255,211,67),'gem':(179,101,255),
            'speed':(66,220,255),'jump':(111,248,139)}
    color=colors.get(kind,(255,255,255))
    pygame.draw.circle(screen,tuple(max(0,int(v*.35)) for v in color),(x,y),23)
    pygame.draw.circle(screen,color,(x,y),16,2)
    if kind=='star':
        pts=[]
        for i in range(10):
            a=-math.pi/2+i*math.pi/5
            r=14 if i%2==0 else 6
            pts.append((x+int(math.cos(a)*r),y+int(math.sin(a)*r)))
        pygame.draw.polygon(screen,color,pts)
    elif kind=='gem':
        pygame.draw.polygon(screen,color,[(x,y-15),(x+13,y-4),(x+8,y+12),(x-8,y+12),(x-13,y-4)])
        pygame.draw.line(screen,(255,224,255),(x,y-13),(x-5,y+9),2)
    elif kind=='speed':
        pygame.draw.polygon(screen,color,[(x+2,y-14),(x-10,y+2),(x-1,y+2),
                                           (x-5,y+15),(x+12,y-4),(x+2,y-4)])
    else:
        pygame.draw.arc(screen,color,(x-11,y-9,22,19),0,math.pi,4)
        pygame.draw.polygon(screen,color,[(x-9,y-5),(x,y-16),(x+9,y-5)])

def avatar(name,p,index,cam,t):
    x=int(p['x']-cam);y=int(p['y']);color=PALETTE[index%4]
    if x<-80 or x>SW+80:return
    moving=abs(p.get('vx',0))>25
    grounded=abs(p.get('vy',0))<25
    bounce=math.sin(t*15)*2 if moving and grounded else 0
    y+=int(bounce)
    if p.get('invuln',0)>.05 and int(t*12)%2==0:return
    # Draw the player's chosen pixel-art sprite (nearest-neighbour scaling).
    choice=(room or {}).get('characters',{}).get(name,CHARACTERS[index%4])
    sprite=character_images.get(choice)
    if sprite is not None:
        facing=int(p.get('facing',1))
        speed=abs(float(p.get('vx',0)))
        frames=run_frames.get(choice)
        if frames:
            # Use actual four-frame leg/arm running cycle instead of wobbling a still pose.
            if moving and grounded:
                frame_index=int(t*(8.0+min(speed/70.0,5.0)))%4
            else:
                frame_index=1 if grounded else 0
            sprite=frames[frame_index]
        if facing<0:sprite=pygame.transform.flip(sprite,True,False)
        # Keep pixel edges crisp: don't rotate or stretch the new pixel sprites.
        pygame.draw.ellipse(screen,(41,94,96),(x-8,y+44,45,8))
        foot_y=y+43
        screen.blit(sprite,(x+14-sprite.get_width()//2,foot_y-sprite.get_height()))
        label=FONTS[16].render(name+(' ★' if name==USERNAME else ''),True,(255,255,255))
        plate=label.get_rect(midbottom=(x+14,y-23)).inflate(14,7)
        pygame.draw.rect(screen,(30,54,77),plate,border_radius=7)
        screen.blit(label,label.get_rect(center=plate.center))
        return
    # shadow
    pygame.draw.ellipse(screen,(41,94,96),(x-1,y+44,36,8))
    # legs + boots
    step=math.sin(t*17)*5 if moving and grounded else 0
    rect((51,61,100),(x+4,y+32+step,11,14),3)
    rect((51,61,100),(x+19,y+32-step,11,14),3)
    rect((246,238,203),(x+1,y+42+step,16,6),2)
    rect((246,238,203),(x+18,y+42-step,16,6),2)
    # body with backpack
    rect((47,64,100),(x-4,y+13,12,22),5)
    rect(color,(x+2,y+13,30,24),8)
    rect(tuple(min(255,c+35) for c in color),(x+6,y+14,21,6),3)
    # arms
    rect((246,196,153),(x-3,y+19,8,13),4)
    rect((246,196,153),(x+28,y+19,8,13),4)
    # head + hair + visor
    rect((248,207,163),(x+6,y+1,22,20),7)
    rect((59,54,81),(x+4,y-3,25,9),4)
    rect(color,(x+1,y-4,32,7),3)
    face=int(p.get('facing',1))
    ex=x+(22 if face>=0 else 11)
    pygame.draw.circle(screen,(42,50,71),(ex,y+10),2)
    pygame.draw.circle(screen,(249,245,239),(ex+1,y+9),1)
    # nameplate
    label=FONTS[16].render(name+(' ★' if name==USERNAME else ''),True,(255,255,255))
    plate=label.get_rect(midbottom=(x+17,y-11)).inflate(14,7)
    pygame.draw.rect(screen,(30,54,77),plate,border_radius=7)
    screen.blit(label,label.get_rect(center=plate.center))

def panel(x,y,w,h,alpha=220):
    surf=pygame.Surface((int(w),int(h)),pygame.SRCALPHA)
    pygame.draw.rect(surf,(22,44,70,alpha),surf.get_rect(),border_radius=18)
    pygame.draw.rect(surf,(130,203,231,100),surf.get_rect(),2,border_radius=18)
    screen.blit(surf,(int(x),int(y)))

def draw_scene(t,dt):
    global camera
    w=world
    if w:
        own=w['players'].get(USERNAME)
        if own:
            desired=max(0,own['x']-SW*.45)
            camera+=(desired-camera)*min(1,dt*5)
    background(t,camera)
    if w:
        gy=w['ground_y']
        ground(camera,gy)
        for x,y,ww,hh in w['platforms']:platform(x,y,ww,hh,camera)
        for x,y,ww,hh in w.get('hazards',[]):spikes(x,y,ww,hh,camera)
        for h in w.get('moving_hazards',[]):moving_saw(h,camera,t)
        for c in w['coins']:coin(c['x'],c['y'],t,camera)
        for item in w.get('pickups',[]):draw_pickup(item,camera,t)
        names=sorted(w['players'])
        for i,name in enumerate(names):
            p=w['players'][name]
            target=(p['x'],p['y'])
            old=display_positions.get(name,target)
            # Avoid smoothing huge teleports (respawn); interpolate ordinary motion.
            if abs(old[0]-target[0])>200 or abs(old[1]-target[1])>180:old=target
            a=min(1,dt*16)
            smooth=(old[0]+(target[0]-old[0])*a,old[1]+(target[1]-old[1])*a)
            display_positions[name]=smooth
            avatar(name,dict(p,x=smooth[0],y=smooth[1]),i,camera,t)
        for e in w.get('effects',[]):
            if e['type']=='coin':
                text('+10',e['x']-camera,e['y']-45,24,(255,248,148),center=True,shadow=True)
            elif e['type']=='pickup':
                label={'star':'+50 STAR!', 'gem':'+25 GEM!',
                       'speed':'SPEED BOOST!', 'jump':'SUPER JUMP!'}.get(e['kind'],'BONUS!')
                text(label,e['x']-camera,e['y']-58,20,(255,238,133),center=True,shadow=True)
        draw_particles(camera)
        for name in list(display_positions):
            if name not in w['players']:display_positions.pop(name,None)
    else:
        # Large decorative platform scene behind menu.
        for x,y,ww in [(90,530,240),(445,450,210),(775,550,230)]:platform(x,y,ww,22,0)
        for x,y in [(200,480),(530,400),(875,500)]:coin(x,y,t,0)
    if world and page=='playing':biome_transition(t)
    # Compact, non-overlapping HUD; the timer and boost pills have dedicated space.
    if world:
        panel(14,12,SW-28,76,195)
        text('SUPER PLATFORM ARENA',31,23,24,(255,237,160),shadow=True)
        text(current_biome()['name'],32,58,16,(208,239,250))
        time_label='--' if remaining is None else str(max(0,int(remaining)))+'s'
        text('TIME '+time_label,SW-170,30,24,(255,236,146),shadow=True)
        ordered=sorted(scores.items(),key=lambda kv:(-kv[1],kv[0]))
        board_height=43+len(ordered)*29
        panel(14,100,263,board_height,183)
        text('LEADERBOARD',29,112,20,(255,238,160))
        for i,(name,points) in enumerate(ordered):
            text(f'{i+1}. {name[:11]:11} {points:>4}',29,143+i*29,18,
                 (255,237,149) if name==USERNAME else (235,247,255))
        own=w['players'].get(USERNAME,{})
        elapsed=float(w.get('elapsed',0))
        buffs=[]
        if own.get('speed_until',0)>elapsed:
            buffs.append(('SPEED',own['speed_until']-elapsed,(69,213,255)))
        if own.get('jump_until',0)>elapsed:
            buffs.append(('SUPER JUMP',own['jump_until']-elapsed,(131,243,148)))
        for i,(label,left,color) in enumerate(buffs):
            bx=SW-236;by=105+i*53
            panel(bx,by,220,46,190)
            text(f'{label}  {math.ceil(left)}s',bx+13,by+7,18,color)
            rect((45,68,83),(bx+13,by+32,192,5),3)
            maximum=7 if label=='SPEED' else 9
            rect(color,(bx+13,by+32,max(1,int(192*min(1,left/maximum))),5),3)
        text('A/D MOVE    SPACE JUMP    ESC MENU    M MUTE',SW//2,SH-40,16,
             (255,255,236),center=True,shadow=True)
        text('SOUND ON' if SOUND_ENABLED else 'SOUND OFF',SW-135,SH-70,16,
             (171,241,188) if SOUND_ENABLED else (255,176,168))
    if time.monotonic()-notice_time<5:
        panel(18,SH-108,SW-36,32,165)
        text(notice[:110],35,SH-101,16,(255,236,169))


def add_button(label,x,y,w,h,action,enabled=True):
    r=pygame.Rect(int(x),int(y),int(w),int(h))
    buttons.append((r,action,enabled))
    hover=r.collidepoint(pygame.mouse.get_pos())
    color=(53,145,178) if enabled and hover else ((38,111,157) if enabled else (68,80,99))
    rect(color,(r.x,r.y,r.w,r.h),12)
    pygame.draw.rect(screen,(155,219,234) if enabled else (92,104,121),r,2,border_radius=12)
    text(label,r.centerx,r.centery,20,(255,255,255) if enabled else (175,183,194),center=True)


def add_field(key,label,x,y,w=440,secret=False):
    global active_field
    value=fields.get(key,'')
    text(label,x,y-27,18,(224,240,249))
    r=pygame.Rect(int(x),int(y),int(w),48)
    rect((21,44,68),(r.x,r.y,r.w,r.h),9)
    pygame.draw.rect(screen,(251,218,117) if active_field==key else (117,169,195),r,2,border_radius=9)
    visible='•'*len(value) if secret else value
    text((visible or 'Click to type')[:37],x+13,y+12,20,(255,255,255) if value else (139,169,185))
    return r


def countdown_overlay(t):
    """Server-driven countdown; locally extrapolated between UDP snapshots."""
    if page != 'playing' or match_id is None:
        return
    left=max(0.0, countdown_remaining - max(0.0, t-countdown_last_update))
    if left <= 0 and (world is None or world.get('elapsed',0) >= 0.85):
        return
    veil=pygame.Surface((SW,SH),pygame.SRCALPHA)
    veil.fill((12,19,39,112 if left>0 else 65))
    screen.blit(veil,(0,0))
    if left>0:
        number=str(min(3,max(1,math.ceil(left-0.35)))) if left>0.35 else 'GO!'
    else:
        number='GO!'
    font=pygame.font.SysFont('consolas',max(70,min(145,int(SH*.17))),bold=True)
    color=(255,235,150) if number!='GO!' else (139,255,181)
    shadow=font.render(number,True,(15,25,44))
    label=font.render(number,True,color)
    r=label.get_rect(center=(SW//2,SH//2-20))
    screen.blit(shadow,r.move(5,7))
    screen.blit(label,r)
    text('GET READY!' if left>0.35 else 'COLLECT THE MOST POINTS',SW//2,SH//2+95,24,(244,250,255),center=True,shadow=True)


def victory_results(t):
    """Dedicated responsive results view, supporting ties and up to four players."""
    global buttons
    buttons=[]
    ranking=(result or {}).get('ranking',[])
    winners=(result or {}).get('winners',[])
    if not winners and ranking:
        top=ranking[0]['score']
        winners=[r['username'] for r in ranking if r['score']==top]
    cx=SW//2
    panel(cx-330,55,660,SH-102,244)
    if winners:
        title='IT\'S A TIE!' if len(winners)>1 else 'CHAMPION!'
        text(title,cx,92,42,(255,229,128),center=True,shadow=True)
        winner=winners[0]
        text(' & '.join(winners)[:35],cx,154,24,(248,251,255),center=True)
        # Winner sprite uses the character choice saved in the room.
        choice=(room or {}).get('characters',{}).get(winner,'Mario')
        sprite=character_images.get(choice)
        if sprite:
            scaled=pygame.transform.scale(sprite,(int(sprite.get_width()*1.65),int(sprite.get_height()*1.65)))
            screen.blit(scaled,scaled.get_rect(center=(cx,244)))
        else:
            text('\u2605',cx,240,58,(255,225,125),center=True)
        # Stable, lightweight confetti around the top section.
        colors=[(255,211,95),(112,231,219),(246,131,174),(171,151,255)]
        for j in range(72):
            x=int(cx-310+(j*73)%620+math.sin(t*1.4+j)*10)
            y=int(80+(j*47+int(t*35))%245)
            pygame.draw.rect(screen,colors[j%4],(x,y,5+(j%3),3+(j%2)))
    else:
        text('MATCH COMPLETE',cx,100,30,(255,229,128),center=True)
    text('FINAL RANKINGS',cx,326,24,(234,244,255),center=True)
    for i,r in enumerate(ranking[:4]):
        yy=367+i*48
        pygame.draw.rect(screen,(56,94,113) if i==0 else (36,65,87),
                         (cx-260,yy,520,43),border_radius=10)
        name=str(r.get('username','?'))[:18]
        rank=r.get('rank',i+1)
        text(f'#{rank}   {name}',cx-239,yy+10,20,(255,231,150) if i==0 else (239,248,255))
        points=str(r.get('score',0))+' pts'
        text(points,cx+237-FONTS[20].size(points)[0],yy+10,20,(246,248,255))
    add_button('BACK TO ROOM',cx-220,SH-92,210,48,'back_to_room')
    add_button('LEAVE ROOM',cx+10,SH-92,210,48,'leave')


def draw_menu():
    global buttons, field_rects
    buttons=[];field_rects={}
    if page=='playing':return
    if page=='results':
        victory_results(time.monotonic())
        return
    panel(SW//2-305,105,610,SH-160,235)
    text('SUPER PLATFORM ARENA',SW//2,149,30,(255,238,160),center=True,shadow=True)
    cx=SW//2
    if page=='login':
        text('CONNECT & PLAY',cx,205,24,center=True)
        x=cx-220
        for key,label,yy,secret in [('host','Server IP',263,False),('username','Username',349,False),('password','Password',435,True)]:
            field_rects[key]=add_field(key,label,x,yy,440,secret)
        add_button('LOGIN',cx-220,518,210,54,'login')
        add_button('REGISTER',cx+10,518,210,54,'register')
        text('Same Wi-Fi? Enter the server computer IP.',cx,598,16,(198,226,241),center=True)
    elif page=='lobby':
        text('WELCOME, '+USERNAME[:20],cx,205,24,center=True)
        x=cx-220
        field_rects['room']=add_field('room','Room name',x,279,440)
        add_button('CREATE ROOM',cx-220,365,210,54,'create')
        add_button('JOIN ROOM',cx+10,365,210,54,'join')
        add_button('REFRESH ROOMS',cx-220,437,440,50,'refresh')
        text('AVAILABLE ROOMS',cx,517,20,(255,237,155),center=True)
        for i,r in enumerate(rooms_list[:3]):
            name=r.get('name','?') if isinstance(r,dict) else str(r)
            text(name[:28],cx-204,554+i*28,18,(222,241,255))
            add_button('JOIN',cx+115,550+i*28,95,25,'join:'+name)
    elif page=='results':
        # Dedicated results page: no lobby/character controls overlap rankings.
        text('MATCH COMPLETE',cx,216,30,(255,229,135),center=True,shadow=True)
        ranking=(result or {}).get('ranking',[])
        if ranking:
            winner=ranking[0]
            text('WINNER',cx,271,20,(255,229,135),center=True)
            text(str(winner.get('username','?'))[:22],cx,308,30,(255,255,255),center=True)
            text('FINAL RANKINGS',cx,369,20,(225,238,255),center=True)
            for i,r in enumerate(ranking[:4]):
                yy=408+i*43
                pygame.draw.rect(screen,(39,76,99) if i%2==0 else (31,64,88),
                                 (cx-230,yy-6,460,38),border_radius=8)
                rank=r.get('rank',i+1)
                text(f'#{rank}  {str(r.get("username","?"))[:18]}',cx-211,yy,20,
                     (255,232,155) if i==0 else (240,246,255))
                points=str(r.get('score',0))+' pts'
                text(points,cx+210-FONTS[20].size(points)[0],yy,20,(240,246,255))
        else:
            text('Waiting for final scores...',cx,340,20,center=True)
        add_button('BACK TO ROOM',cx-220,SH-91,210,49,'back_to_room')
        add_button('LEAVE ROOM',cx+10,SH-91,210,49,'leave')
    elif page=='room':
        text('ROOM: '+str(room.get('name',ROOM))[:22] if room else 'ROOM',cx,211,24,center=True)
        if room:
            text('PLAYERS',cx,270,20,(255,232,156),center=True)
            for i,name in enumerate(room.get('players',[])[:4]):
                ready=name in room.get('ready',[])
                choice=room.get('characters',{}).get(name,'Mario')
                text(name[:13] + ' ['+choice+']' + (' READY' if ready else ' WAITING'),cx,312+i*36,18,
                     (149,243,180) if ready else (235,241,249),center=True)
        text('CHOOSE CHARACTER',cx,SH-250,18,(255,232,156),center=True)
        for i,c in enumerate(CHARACTERS):
            add_button(c.upper(),cx-225+i*115,SH-224,105,35,'character:'+c)
        my_ready=bool(room and USERNAME in room.get('ready',[]))
        add_button('UNREADY' if my_ready else 'READY',cx-220,SH-176,135,48,'ready')
        add_button('START',cx-72,SH-176,135,48,'start')
        add_button('LEAVE',cx+76,SH-176,145,48,'leave')
    elif page=='pause':
        text('PAUSED MENU',cx,240,30,center=True)
        text('The online match continues while this menu is open.',cx,310,16,center=True)
        add_button('RESUME GAME',cx-180,385,360,58,'resume')
        add_button('LEAVE ROOM',cx-180,465,360,58,'leave')


def activate(action):
    global page,result,ROOM
    if action in ('login','register'):
        begin_connect(fields.get('host','127.0.0.1'),fields.get('username',''),fields.get('password',''),action=='register')
    elif action=='create':
        ROOM=fields.get('room','').strip() or 'Arena1';send_tcp({'type':'create_room','room':ROOM})
    elif action=='join' or action.startswith('join:'):
        ROOM=action.split(':',1)[1] if ':' in action else (fields.get('room','').strip() or 'Arena1')
        fields['room']=ROOM;send_tcp({'type':'join_room','room':ROOM})
    elif action.startswith('character:'):send_tcp({'type':'select_character','character':action.split(':',1)[1]})
    elif action=='refresh':send_tcp({'type':'list_rooms'})
    elif action=='ready':
        send_tcp({'type':'ready','ready':USERNAME not in (room.get('ready',[]) if room else [])})
        result=None
    elif action=='start':send_tcp({'type':'start_game'})
    elif action=='leave':send_tcp({'type':'leave_room'})
    elif action=='resume':page='playing'
    elif action=='back_to_room':page='room'


def set_notice(value):
    global notice,notice_time
    notice=str(value);notice_time=time.monotonic()

fields={'host':'127.0.0.1','username':'','password':'','room':'Arena1'}
field_rects={}
while running:
    dt=min(clock.tick(60)/1000,.05);t=time.monotonic()
    for event in pygame.event.get():
        if event.type==pygame.QUIT:running=False
        elif event.type==pygame.VIDEORESIZE:
            SW,SH=max(850,event.w),max(700,event.h)
            screen=pygame.display.set_mode((SW,SH),pygame.RESIZABLE)
        elif event.type==pygame.MOUSEBUTTONDOWN and event.button==1:
            if page!='playing':
                active_field=next((k for k,r in field_rects.items() if r.collidepoint(event.pos)),None)
                if active_field is None:
                    for r,action,enabled in buttons:
                        if enabled and r.collidepoint(event.pos):activate(action);break
        elif event.type==pygame.KEYDOWN:
            if event.key==pygame.K_m and page in ('playing','pause'):
                SOUND_ENABLED=not SOUND_ENABLED
                set_notice('Sound '+('enabled' if SOUND_ENABLED else 'muted'))
            elif event.key==pygame.K_ESCAPE:
                if page=='playing':page='pause'
                elif page=='pause':page='playing'
            elif event.key==pygame.K_F11:pygame.display.toggle_fullscreen()
            elif active_field is not None and page in ('login','lobby'):
                if event.key==pygame.K_BACKSPACE:fields[active_field]=fields[active_field][:-1]
                elif event.key==pygame.K_TAB:
                    order=['host','username','password'] if page=='login' else ['room']
                    active_field=order[(order.index(active_field)+1)%len(order)]
                elif event.key==pygame.K_RETURN:
                    activate('login' if page=='login' else 'join')
                elif event.unicode.isprintable() and len(fields[active_field])<64:
                    fields[active_field]+=event.unicode
    for _ in range(150):
        try:item=messages.get_nowait()
        except queue.Empty:break
        channel,p,generation=item
        if generation!=connect_generation:continue
        kind=p.get('type')
        if kind=='connected':
            tcp=p['socket'];connected=True
            threading.Thread(target=tcp_reader,args=(tcp,generation),daemon=True).start()
            pending_register=p['register']
            send_tcp({'type':'register' if pending_register else 'login','username':USERNAME,'password':PASSWORD})
        elif kind=='connect_failed':set_notice('Connection failed: '+p.get('message',''))
        elif kind=='registered':
            send_tcp({'type':'login','username':USERNAME,'password':PASSWORD})
        elif kind=='logged_in':
            authenticated=True;udp_token=p['udp_token'];start_udp(p)
            page='lobby';send_tcp({'type':'list_rooms'});set_notice('Logged in as '+USERNAME)
        elif kind in ('room_update','room_created','room_joined'):
            room=p.get('room',room)
            if isinstance(room,dict):
                ROOM=room.get('name',ROOM)
                if room.get('status')=='waiting':
                    world=None;match_id=None
                    if page not in ('results',):page='room'
        elif kind in ('rooms','rooms_list','room_list'):
            rooms_list=p.get('rooms',[])
        elif kind=='ready_updated':
            if room is not None:
                ready=set(room.get('ready',[]))
                if p.get('ready'):ready.add(USERNAME)
                else:ready.discard(USERNAME)
                room['ready']=sorted(ready)
        elif kind=='room_left':
            room=None;world=None;match_id=None;result=None;page='lobby'
            send_tcp({'type':'list_rooms'});set_notice('Left room')
        elif kind=='game_started':
            match_id=p['match_id'];scores=p.get('scores',{});world=None;result=None
            page='playing';camera=0
            visual_particles.clear();seen_effects.clear();last_player_state=None
            last_jump_key=False;last_countdown_active=True
            countdown_remaining=float(p.get('countdown_seconds',3.5));countdown_last_update=t
            match_start_local=t+countdown_remaining
            set_notice('MATCH STARTED! Collect more coins than your rivals!')
        elif kind=='game_state' and match_id==p.get('match_id'):
            world=p['world'];scores=p.get('scores',{});remaining=p.get('remaining_seconds');last_snapshot=t
            countdown_remaining=float(p.get('countdown_remaining',0));countdown_last_update=t
            handle_game_effects(world,t)
        elif kind=='score_update':scores=p.get('scores',scores)
        elif kind=='game_ended':
            if page!='results': play_sound('victory')
            result=p;world=None;match_id=None;page='results'
            set_notice('Match finished! Check the rankings.')
        elif kind=='udp_welcome':udp_ok=True;set_notice('UDP connected!')
        elif kind=='error':set_notice('SERVER: '+p.get('message','Error'))
        elif kind=='connection_closed':
            connected=False;authenticated=False
            if tcp:
                try:tcp.close()
                except OSError:pass
            tcp=None
            if udp:
                try:udp.close()
                except OSError:pass
            udp=None;page='login';set_notice('Server disconnected. Please log in again.')
    if authenticated and not udp_ok and t-last_hello>2:
        hello();last_hello=t
    if match_id is not None and udp is not None and t-last_input>=.05:
        keys=pygame.key.get_pressed();seq+=1
        in_countdown=max(0.0,countdown_remaining-(t-countdown_last_update))>0
        jump_pressed=page=='playing' and not in_countdown and bool(keys[pygame.K_SPACE] or keys[pygame.K_w] or keys[pygame.K_UP])
        if jump_pressed and not last_jump_key:
            own=(world or {}).get('players',{}).get(USERNAME,{})
            if own and (abs(own.get('vy',0))<90 or own.get('coyote',0)>0):play_sound('jump')
        last_jump_key=jump_pressed
        packet={'type':'player_input','match_id':match_id,'seq':seq,
                'left':page=='playing' and not in_countdown and bool(keys[pygame.K_LEFT] or keys[pygame.K_a]),
                'right':page=='playing' and not in_countdown and bool(keys[pygame.K_RIGHT] or keys[pygame.K_d]),
                'jump':jump_pressed}
        try:udp.sendto(json.dumps(packet).encode(),server_udp)
        except OSError:pass
        last_input=t
    countdown_active=(match_id is not None and max(0.0,countdown_remaining-(t-countdown_last_update))>0)
    if last_countdown_active and not countdown_active and match_id is not None:play_sound('go')
    last_countdown_active=countdown_active
    update_particles(dt)
    draw_scene(t,dt)
    draw_menu()
    countdown_overlay(t)
    pygame.display.flip()
try:
    if udp:udp.close()
    if tcp:tcp.close()
except OSError:pass
pygame.quit()
