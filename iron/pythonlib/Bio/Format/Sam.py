import Bio.Format.PSL
import Bio.Sequence
from FileBasics import GenericFileReader
import re, sys, os
import subprocess
from Bio.Range import Bed
from multiprocessing import Pool

#Globals
_cigar_prog = re.compile('([0-9]+)([A-Z])')
_target_sequence_prog = re.compile('^[MDNX=]$')


class SAMtoPSLconversionFactory:
  def __init__(self):
    self.reads = {}
    self.genome_lengths = {}
    self.genome = None
    self.length_warned = False
  def set_genome(self,fasta):
    self.genome = Bio.Sequence.read_fasta_into_hash(fasta)
  def read_header_line(self,hline):
    m = re.match('^@SQ\s+SN:(\S+)\s+LN:(\d+)',hline)
    if not m: return
    self.genome_lengths[m.group(1)] = int(m.group(2))
  def convert_line(self,line):
    line = line.rstrip()
    d = sam_line_to_dictionary(line)
    if d['rname'] == '*': return None
    if d['cigar'] == '*': return None
    matches = 0
    misMatches = 0
    repMatches = 0
    nCount = 0
    qNumInsert = 0
    qBaseInsert = 0
    tNumInsert = 0
    tBaseInsert = 0
    strand = get_entry_strand(d)
    qName = d['qname']
    #qSize = len(d['seq']) # not accurate when seq is '*'
    qStart = 0
    qEnd = 0
    tName = d['rname']
    tSize = 0
    if d['rname'] in self.genome_lengths:
      tSize = self.genome_lengths[d['rname']]
    tStart = d['pos']-1
    tEnd = 0
    blockCount = 0
    blockSizes = ''
    qStarts = ''
    tStarts = ''

    trim_offset = 0 # this is the number of bases from the query we need to put back during position reporting
    right_trim = 0

    qSize = 0
    working_seq = d['seq']
    working_cigar = d['cigar_array'][:]
    working_tStart = tStart
    # deal with soft clipping at the start
    # These are present in seq but should be removed
    if len(working_cigar) > 0:
      if working_cigar[0]['op'] == 'S':
        #print "soft clipped 5'"
        if working_seq != '*':
          working_seq = working_seq[working_cigar[0]['val']:]
        trim_offset = working_cigar[0]['val']
        working_cigar = working_cigar[1:] #take off the element from our cigar

    # deal with soft clipping at the end
    # These are present in seq but should be removed
    if len(working_cigar) > 0:
      if working_cigar[len(working_cigar)-1]['op'] == 'S':
        #print "soft clipped 3'"
        if working_seq != '*':
          working_seq = working_seq[:-1*working_cigar[len(working_cigar)-1]['val']]
        right_trim = working_cigar[len(working_cigar)-1]['val']
        working_cigar = working_cigar[:-1]

    # deal with hard clipping at the start
    #  Not present in seq and can basically be ignored
    #hard5 = 0
    if len(working_cigar) > 0:
      if working_cigar[0]['op'] == 'H':
        #hard5 = working_cigar[0]['val']
        #print "hard clipped 5'"
        #sys.exit()
        trim_offset = working_cigar[0]['val']
        working_cigar = working_cigar[1:]

    # deal with hard clipping at the end
    #  Not present in seq and can basically be ignored
    #hard3 = 0
    if len(working_cigar) > 0:
      if working_cigar[-1]['op'] == 'H':
        #hard3 = working_cigar[-1]['val']
        #print "hard clipped 3'"
        #sys.exit()
        right_trim = working_cigar[-1]['val']
        working_cigar = working_cigar[:-1]

    qSize = trim_offset + right_trim # add on whatever hard or soft clipping we are ignoring in the subsequent parsing
    # Values for traversing the CIGAR
    current_seq_pos = 0
    current_ref_pos = working_tStart

    seq_pos_end = 0
    ref_pos_end = 0
    match_count = 0
    mismatch_count = 0
    query_insert_count = 0
    query_insert_bases = 0
    target_insert_count = 0
    target_insert_bases = 0
    n_count = 0
    for entry in working_cigar:
      #print entry
      if re.match('[DN]',entry['op']):
        current_ref_pos += entry['val']
        if re.match('[D]',entry['op']): 
          query_insert_count += 1
          query_insert_bases += entry['val']
      elif re.match('[I]',entry['op']):
        qSize += entry['val']
        current_seq_pos += entry['val']
        target_insert_count += 1
        target_insert_bases += entry['val']
      elif re.match('[P]',entry['op']):
        sys.stderr.write("ERROR PADDING NOT YET SUPPORTED\n")
        return
      elif re.match('[MX=]',entry['op']):
        qSize += entry['val']
        if working_seq != '*':
          obs = working_seq[current_seq_pos:current_seq_pos+entry['val']].upper()
        #print obs
        #matchlen = len(obs)
        matchlen = entry['val']
        seq_pos_end = current_seq_pos + matchlen
        ref_pos_end = current_ref_pos + matchlen
        qStarts += str(current_seq_pos+trim_offset)+','
        tStarts += str(current_ref_pos)+','
        blockSizes += str(matchlen) + ','
        blockCount += 1
        if self.genome and working_seq != '*':
          if tName not in self.genome:
            sys.stderr.write("ERROR "+tName+" not in reference genome\n")
            return
          act = self.genome[tName][current_ref_pos:current_ref_pos+entry['val']].upper()
          if len(obs) != len(act):
            if not self.length_warned:
              sys.stderr.write("WARNING length mismatch between target and query.  Additional warnings about this are suppressed\n")
              self.length_warned = True
          if len(obs) > len(act):
            sys.stderr.write("ERROR length of observed is greater than reference\n")
            return None
          for i in range(0,len(obs)):
            if obs[i] == 'N': n_count += 1
            if obs[i] == act[i]: match_count+=1
            else: mismatch_count+=1
        elif working_seq != '*':
          for i in range(0,len(obs)):
            if obs[i] == 'N': n_count += 1
            else: match_count += 1
        else:
          match_count += matchlen
        #print tName
        #print current_ref_pos
        current_ref_pos += entry['val']
        current_seq_pos += entry['val']
    oline =  str(match_count) + "\t" + str(mismatch_count) + "\t" + str(repMatches) + "\t" 
    oline += str(n_count) + "\t" + str(query_insert_count) + "\t" + str(query_insert_bases) + "\t"
    oline += str(target_insert_count) + "\t" + str(target_insert_bases) + "\t"
    oline += strand + "\t" + qName + "\t" + str(qSize) + "\t" + str(trim_offset) + "\t"
    oline += str(trim_offset+seq_pos_end) + "\t" + tName + "\t" + str(tSize) + "\t" + str(working_tStart) + "\t" 
    oline += str(ref_pos_end) + "\t" + str(blockCount) + "\t" + blockSizes + "\t"
    oline += qStarts + "\t" + tStarts
    return oline

class PSLtoSAMconversionFactory:
  # Based on the 3 Mar 2015 Sam Specification
  # Can take a lot of RAM because of needing to store the fasta
  def __init__(self):
    self.reads = {}
    self.qualities = {}
    self.min_intron_size = 68
    self.reads_set = False
    self.qualities_set = False
    self.mapping_counts_set = False
    self.ref_genome_set = False
    self.skip_directionless_splice = False
    self.set_canon()
    self.set_revcanon()
  def set_skip_directionless_splice(self):
    self.skip_directionless_splice = True
  def set_canon(self):
    v = set()
    v.add('GT-AG')
    v.add('GC-AG')
    v.add('AT-AC')
    self.canonical = v
  def set_revcanon(self):
    v = set()
    v.add('CT-AC')
    v.add('CT-GC')
    v.add('GT-AT')
    self.revcanonical = v
  def set_mapping_counts(self,psl_filename):
    self.mapping_counts_set = True
    gfr0 = GenericFileReader(psl_filename)
    qcnts = {}
    while True:
      line = gfr0.readline()
      if not line: break
      try:
        psle = Bio.Format.PSL.line_to_entry(line.rstrip())
      except:
        sys.stderr.write("Problem parsing line:\n"+line.rstrip()+"\n")
        continue
      if psle['qName'] not in qcnts: qcnts[psle['qName']] = 0
      qcnts[psle['qName']] += 1
    gfr0.close()
    self.mapping_counts = qcnts

  def set_min_intron_size(self,intron_size):
    self.min_intron_size = intron_size

  def set_reference_genome(self,ref_genome):
    self.ref_genome_set = True
    self.ref_genome = Bio.Sequence.read_fasta_into_hash(ref_genome)

  def convert_line(self,psl_line,query_sequence=None,quality_sequence=None):
    try:
      pe = Bio.Format.PSL.line_to_entry(psl_line)
    except:
      sys.stderr.write("Problem parsing line:\n"+psl_line.rstrip()+"\n")
      return False
    if len(pe['tStarts']) != len(pe['blockSizes']):
      sys.stderr.write("Warning invalid psl entry: "+pe['qName']+"\n")
      return False
    #work on the positive strand case first
    cigar = '*'
    blocks = len(pe['blockSizes'])
    starts = pe['qStarts']
    #if pe['strand'] == '-':
    #  starts = [x for x in reversed(pe['qStarts_actual'])]
    #  print 'isrev'
    q_coord_start = starts[0]+1 # base-1 converted starting position
    q_coord_end = starts[blocks-1]+pe['blockSizes'][blocks-1] # base-1 position
    t_coord_start = pe['tStarts'][0]+1 # base-1 converted starting position
    t_coord_end = pe['tStarts'][blocks-1]+pe['blockSizes'][blocks-1] # base-1 position
    if pe['qName'] not in self.reads and self.reads_set is True:
      sys.stderr.write("Warning: qName "+pe['qName']+" was not found in reads\n")
    # we will clip the query sequence to begin and end from the aligned region
    #q_seq = ''
    #if self.reads_set:
    #  q_seq = self.reads[pe['qName']]

    # 1. Get the new query to output
    q_seq_trimmed = '*'
    if self.reads_set or query_sequence:
      q_seq_trimmed = query_sequence
      if not query_sequence: # get it from the archive we loaded if we didn't give it
        q_seq_trimmed = self.reads[pe['qName']]
      if pe['strand'] == '-':
        q_seq_trimmed = Bio.Sequence.rc(q_seq_trimmed)
      q_seq_trimmed = q_seq_trimmed[q_coord_start-1:q_coord_end]

    qual_trimmed = '*'
    if self.qualities_set or quality_sequence:
      qual_trimmed = quality_sequence
      if not quality_sequence:
        qual_trimmed = self.qualities[pe['qName']]
      if pe['strand'] == '-':
        qual_trimmed = qual_trimmed[::-1]
      qual_trimmed = qual_trimmed[q_coord_start-1:q_coord_end]
    # 2. Get the cigar string to output
    prev_diff = t_coord_start-q_coord_start
    cigar = ''
    #for i in range(0,blocks):
    #  current_diff = pe['tStarts'][i]-starts[i]
    #  delta = current_diff - prev_diff
    #  #print delta
    #  if delta >= self.min_intron_size:
    #    cigar += str(abs(delta))+'N'
    #  elif delta > 0: # we have a
    #    cigar += str(abs(delta))+'D'
    #  elif delta < 0: # we have a
    #    cigar += str(abs(delta))+'I'
    #  cigar += str(pe['blockSizes'][i])+'M' # our matches
    #  #print current_diff
    #  prev_diff = current_diff
    qstarts = [x-pe['qStarts'][0] for x in pe['qStarts']]
    tstarts = [x-pe['tStarts'][0] for x in pe['tStarts']]
    query_index = 0
    target_index = 0
    junctions = []
    for i in range(0,blocks):
      qdif = qstarts[i] - query_index
      tdif = tstarts[i] - target_index
      if qdif > 0:  # we have to insert
        cigar += str(qdif) + 'I'
      if tdif > self.min_intron_size: # we have an intron
        cigar += str(tdif) + 'N'
        junctions.append(i)
      elif tdif > 0: # we have to delete
        cigar += str(tdif) + 'D'
      cigar += str(pe['blockSizes'][i]) + 'M'
      query_index = qstarts[i]+pe['blockSizes'][i]
      target_index = tstarts[i]+pe['blockSizes'][i]
    ### cigar done
    # inspect junctions if we have a ref_genome
    spliceflag_set = False
    if self.ref_genome_set:
      canon = 0
      revcanon = 0
      for i in junctions: #blocks following a junction
        left_num = pe['tStarts'][i-1]+pe['blockSizes'][i-1]
        left_val = self.ref_genome[pe['tName']][left_num:left_num+2].upper()
        right_num = pe['tStarts'][i-1]-2
        right_val = self.ref_genome[pe['tName']][right_num:right_num+2].upper()
        junc = left_val + '-' + right_val
        if junc in self.canonical: canon += 1
        if junc in self.revcanonical: revcanon += 1
      if canon > revcanon: 
        spliceflag_set = True
        spliceflag = '+'
      elif revcanon > canon:
        spliceflag_set = True
        spliceflag = '-'
    # if we have junctions, and we should be setting direction but 
    # we can't figure out the direction skip ambiguous direction
    if len(junctions) > 0 and self.skip_directionless_splice and spliceflag_set == False:
      return False
    samline =  pe['qName'] + "\t"        # 1. QNAME
    if pe['strand'] == '-':
      samline += '16' + "\t"             # 2. FLAG
    else:
      samline += '0' + "\t"
    samline += pe['tName'] + "\t"        # 3. RNAME
    samline += str(t_coord_start) + "\t" # 4. POS
    samline += '0' + "\t"                # 5. MAPQ
    samline += cigar + "\t"         # 6. CIGAR
    samline += '*' + "\t"           # 7. RNEXT
    samline += '0' + "\t"           # 8. PNEXT
    samline += '0' + "\t"           # 9. TLEN
    samline += q_seq_trimmed + "\t" # 10. SEQ
    samline += qual_trimmed + "\t"  # 11. QUAL
    if spliceflag_set:
      samline += 'XS:A:'+spliceflag + "\t"
    if self.ref_genome_set:
      samline += 'NH:i:'+str(self.mapping_counts[pe['qName']]) + "\t"
    samline += 'XC:i:'+str(len(junctions)) + "\t"
    samline += 'NM:i:0'
    return samline
  def set_read(self,name,seq):
    self.reads_set = True
    if not self.reads: self.reads = {}
    self.reads[name] = seq.upper()
  def remove_read(self,name):
    if not self.reads_set: return
    if name in self.reads:
      del self.reads[name]
  def set_read_fasta(self,read_fasta_file):
    self.reads_set = True
    gfr = Bio.Sequence.GenericFastaFileReader(read_fasta_file)
    self.reads = {}
    while True:
      e = gfr.read_entry()
      if not e: break
      if e['name'] in self.reads:
        sys.stderr.write("Warning duplicate name in fasta file, could be big problems on sequence assignment.\n")
      self.reads[e['name']] = e['seq'].upper()
    gfr.close()
    return
  def set_read_fastq(self,read_fastq_file):
    self.reads_set = True
    self.qualities_set = True
    gfr = Bio.Sequence.GenericFastqFileReader(read_fastq_file)
    self.reads = {}
    self.qualities = {}
    while True:
      e = gfr.read_entry()
      if not e: break
      if e['name'] in self.reads:
        sys.stderr.write("Warning duplicate name in fasta file, could be big problems on sequence assignment.\n")
      self.reads[e['name']] = e['seq'].upper()
      self.qualities[e['name']] = e['quality']
    gfr.close()
    return
     

def construct_header_from_reference_fasta(ref_fasta_filename):
  g = Bio.Sequence.read_fasta_into_hash(ref_fasta_filename)
  chrs = {}
  for name in sorted(g):
    chrs[name] = len(g[name])
    sys.stderr.write(name+" is there at length "+str(len(g[name]))+"\n")
  header = ''
  header += "@HD\tVN:1.0\tSO:coordinate\n"
  for chr in sorted(chrs):
    header += "@SQ\tSN:"+chr+"\tLN:"+str(chrs[chr])+"\n"
  header += "@PG\tID:SamBasics.py\tVN:1.0\n"
  return header 



# Pre: Requires an indexed bam file
# 
class RandomAccessCoordinateReader:
  def __init__(self,bam_filename,chromosome,start,finish):
    self.filename = bam_filename
    if not re.search('\.bam$',self.filename): 
      sys.stderr.write("Error: not bam file extension.\n")
      sys.exit()
    if not os.path.isfile(self.filename+'.bai'):
      sys.stderr.write("Error: no index bam.bai file present.\n")
      sys.exit()
    cmd = 'samtools view '+self.filename+' '+chromosome+':'+str(start)+'-'+str(finish)
    args = cmd.split()
    self.process = subprocess.Popen(args,bufsize=0,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  def close(self):
    if self.process:
      self.process.kill()

  def readline(self):
      return self.process.stdout.readline()

  def readentry(self):
    line = self.process.stdout.readline()
    if not line: return line
    return sam_line_to_dictionary(line.rstrip())

# Pre: A sam entry (dictionary)
# Post: a bed file line describing the start and stop
def entry_to_blocked_bed(entry,color):
  if entry['rname'] == '*':  return False
  ostring = entry['rname'] + "\t" + str(entry['pos']-1) + "\t"
  #print entry
  #print ostring
  z = entry['pos']-1
  block_count = 0
  block_starts = []
  block_sizes = []
  for c in entry['cigar_array']:
    # entry maps
    if re.match('[MISX=]',c['op']):
      # here is where we should output
      block_starts.append(z-(entry['pos']-1))
      block_sizes.append(c['val'])
      z += c['val']
      block_count += 1
    # entry is a gap
    if re.match('[DNH]',c['op']):
      z+= c['val']
  endfeature = block_starts[block_count-1]+block_sizes[block_count-1]+(entry['pos']-1)
  ostring += str(endfeature) + "\t" # chromEnd
  ostring += entry['qname'] + "\t" # name
  ostring += "1" + "\t" # score
  strand = get_entry_strand(entry)
  ostring += strand + "\t" # strand
  ostring += str(entry['pos']-1) + "\t" #thickStart
  ostring += str(endfeature) + "\t"
  ostring += color + "\t" #itemRgb
  ostring += str(block_count) + "\t" # block count
  ostring += ",".join([str(x) for x in block_sizes])+"," + "\t"   #blockSizes
  ostring += ",".join([str(x) for x in block_starts])+","  #blockStarts
  #ostring += 
  return ostring

def get_entry_strand(entry):
  if check_flag(entry['flag'],16):
    return '-'
  else:
    return '+'
  #print entry['remainder']
  #if re.search('XS:A:+',entry['remainder']): return '+'
  #elif re.search('XS:A:-',entry['remainder']): return '-'
  #else: 
  #  sys.stderr.write("Error did not find strand information for "+entry['rname']+"\n")
  #  sys.exit()
  #return False


# pre:       A line from a sam file
# post:      a string with the coordiantes of the alignment

def get_coordinates(sam_line):
  f = sam_line.rstrip().split("\t")
  name = f[0]
  coordinate = ''
  if f[2] == '*':
    return [name, coordinate]
  coordinate = f[2]+':'+str(f[3])+':'+f[5]
  return [name,coordinate]


#pre: a flag from a sam file, in integer format
#     a bit to convert, given as a hex number ie 0x10
#post: returns true if the flag is set on
def is_header(line):
  if re.match('^@',line):
    f = line.rstrip().split("\t")
    if(len(f) > 9):
      return False
    return True
  return False


#pre: a flag from a sam file, in integer format
#     a bit to convert, given as a hex number ie 0x10
#post: returns true if the flag is set on
def check_flag(flag,inbit):
  if flag & inbit:
    return True
  return False

def entry_to_line(d):
  oline = ''
  oline += d['qname']+"\t"
  oline += str(d['flag'])+"\t"
  oline += d['rname']+"\t"
  oline += str(d['pos'])+"\t"
  oline += d['mapq']+"\t"
  oline += d['cigar']+"\t"
  oline += d['rnext']+"\t"
  oline += str(d['pnext'])+"\t"
  oline += str(d['tlen'])+"\t"
  oline += d['seq']+"\t"
  oline += d['qual']+"\t"
  oline += d['remainder']
  return oline

#pre: a line from a sam file that is not a header entry
#post: a dictionary with entries named like the manual
def sam_line_to_dictionary(line):
  f = line.rstrip().split("\t")
  d = {}
  d['qname'] = f[0]
  d['flag'] = int(f[1])
  d['rname'] = f[2]
  d['pos'] = int(f[3])
  d['mapq'] = f[4]
  cigar = parse_cigar(f[5])
  d['cigar_array'] = cigar
  d['cigar'] = f[5]
  d['rnext'] = f[6]
  d['pnext'] = int(f[7])
  d['tlen'] = int(f[8])
  d['seq'] = f[9]
  d['qual'] = f[10]
  d['remainder'] = ''
  if len(f) > 11:
    d['remainder'] = " ".join(f[11:])
  return d


# pre: CIGAR string
# post: an array of cigar string entries
def parse_cigar(cigar):
  global _cigar_prog
  return [{'op':x[1],'val':int(x[0])} for x in _cigar_prog.findall(cigar)]

# index 1 coordinates
def get_base_at_coordinate(entry,chr,coord):
  if entry['rname'] != chr:
    return False
  #print chr + "\t" + str(coord)
  z = entry['pos']
  bases = list(entry['seq'])
  b = 0
  for c in entry['cigar_array']:
    # entry maps
    if re.match('[MISX=]',c['op']):
      # here is where we should output
      for i in range(0,c['val']):
        if int(z) == int(coord):
          return bases[b]
        b += 1
        z += 1
    # entry is a gap
    if re.match('[DNH]',c['op']):
      z+= c['val']
  return False

# Take a sam line that has an SA:Z tag
# and return an array of sam lines
def get_secondary_alignments(in_sam_line):
    f = in_sam_line.rstrip().split("\t")
    if len(f) <= 12:
      return [] # move on if theres no optional tags
    enstring = "\t".join(f[x] for x in range(11,len(f)))
    m = re.search('SA:Z:(\S+)',enstring)
    if not m:
      return [] # move on if theres no SA:Z tag
    secondary_alignments = m.group(1)
    aligns = secondary_alignments.split(';')
    bwalike = re.compile('^([^,]+),(\d+),([+-]),([^,]+),(\d+),(\d+)$')
    otherlike = re.compile('^([^,]+),([+-])(\d+),([^,]+),(\d+),(\d+)$')
    otherlike2 = re.compile('^([^,]+),([+-])(\d+),([^,]+),(\d+)$')
    output = []
    for align in aligns:
      if align == '': continue # I guess you can have empty segments and we should ignore them
      m1 = bwalike.match(align)
      m2 = otherlike.match(align)
      m3 = otherlike2.match(align)
      if m1:
	chr = m1.group(1)
        pos = m1.group(2)
        strand = m1.group(3)
        cigar = m1.group(4)
        mapQ = m1.group(5)
        nm = m1.group(6)
      elif m2:
	chr = m2.group(1)
        pos = m2.group(3)
        strand = m2.group(2)
        cigar = m2.group(4)
        mapQ = m2.group(5)
        nm = m2.group(6)
      elif m3:
	chr = m3.group(1)
        pos = m3.group(3)
        strand = m3.group(2)
        cigar = m3.group(4)
        mapQ = m3.group(5)
        nm = 0
      else:
	sys.stderr.write("WARNING: unable to parse secondary alignment\n"+align+"\n")
        sys.exit()
      flag = '0'
      if strand == '-': flag = '16'
      samline= f[0]+"\t"+flag+"\t"+chr+"\t"+pos+"\t"+mapQ+"\t"+cigar+"\t"\
             + "*\t0\t0\t*\t*"
      output.append(samline)
    return output

# Take a sam line that has an XA:Z tag
# and return an array of sam lines
def get_alternative_alignments(in_sam_line):
    f = in_sam_line.rstrip().split("\t")
    if len(f) <= 12:
      return [] # move on if theres no optional tags
    enstring = "\t".join(f[x] for x in range(11,len(f)))
    m = re.search('XA:Z:(\S+)',enstring)
    if not m:
      return [] # move on if theres no SA:Z tag
    secondary_alignments = m.group(1)
    aligns = secondary_alignments.split(';')
    bwalike = re.compile('^([^,]+),(\d+),([+-]),([^,]+),(\d+),(\d+)$')
    otherlike = re.compile('^([^,]+),([+-])(\d+),([^,]+),(\d+),(\d+)$')
    otherlike2 = re.compile('^([^,]+),([+-])(\d+),([^,]+),(\d+)$')
    output = []
    for align in aligns:
      if align == '': continue # I guess you can have empty segments and we should ignore them
      m1 = bwalike.match(align)
      m2 = otherlike.match(align)
      m3 = otherlike2.match(align)
      if m1:
	chr = m1.group(1)
        pos = m1.group(2)
        strand = m1.group(3)
        cigar = m1.group(4)
        mapQ = m1.group(5)
        nm = m1.group(6)
      elif m2:
	chr = m2.group(1)
        pos = m2.group(3)
        strand = m2.group(2)
        cigar = m2.group(4)
        mapQ = m2.group(5)
        nm = m2.group(6)
      elif m3:
	chr = m3.group(1)
        pos = m3.group(3)
        strand = m3.group(2)
        cigar = m3.group(4)
        mapQ = m3.group(5)
        nm = 0
      else:
	sys.stderr.write("WARNING: unable to parse secondary alignment\n"+align+"\n")
        sys.exit()
      flag = '0'
      seq = f[9]
      phred = f[10]
      if strand == '-': 
        flag = '16'
        seq = Bio.Sequence.rc(seq)
        phred = phred[::-1]
      samline= f[0]+"\t"+flag+"\t"+chr+"\t"+pos+"\t"+mapQ+"\t"+cigar+"\t"\
             + "*\t0\t0\t*\t*"
      output.append(samline)
    return output

# A generic line for a sam file
class SAM:
  def __init__(self,inline=None):
    self.entry = None
    self.original_line = None
    self.range = None
    if inline: 
      #if is_header(inline):
      #  sys.stderr.write("WARNING: This is a header, not a regular sam line\n")
      #  self = None
      #  return
      self.entry = sam_line_to_dictionary(inline.rstrip())
      self.original_line = inline.rstrip()
    return
  def strand(self):
    return get_entry_strand(self.entry)
  def check_flag(self,num):
    return check_flag(self.entry['flag'],num)
  def value(self,inkey):
    if inkey not in self.entry:
      sys.stderr.write("ERROR: "+inkey+" not set in sam line\n")
      sys.exit()
    return self.entry[inkey]
  def get_line(self):
    return self.original_line
  def get_coverage(self):
    c = 0
    for v in self.value('cigar_array'):
      if v['op'] == 'M': c += v['val']
    return c
  def get_range(self):
    if self.range: return self.range # already set
    global _target_sequence_prog
    #'MDNX=' are the target sequences
    startpos = self.value('pos')-1
    span = sum([x['val'] for x in self.value('cigar_array') if _target_sequence_prog.match(x['op'])])
    self.range = Bed(self.value('rname'),startpos,startpos+span,self.strand())
    return self.range
  # optionally rlens is a dictionary that contains the reference lengths
  # keyed by chromosome name
  def get_psl_line(self,rlens=None):
    spc = SAMtoPSLconversionFactory()
    if rlens: spc.genome_lengths = rlens
    line = spc.convert_line(self.get_line())
    return line

# Pre: Takes a file handle for a sam that is ordered by query
# Post: Return a array of SAM classes for each qname
class MultiEntrySamReader:
  def __init__(self,fh):
    self.fh = fh
    self.buffer = []
    self.header = []
    while True:
      line = self.fh.readline()
      if not line: break
      if is_header(line):
        self.header.append(line.rstrip())
        continue
      s = SAM(line)
      self.buffer.append(s)
      break
  def read_entries(self):
    if len(self.buffer) == 0: return False # we are done
    cname = self.buffer[0].value('qname')
    while True:
      line = self.fh.readline()
      if not line:
        # end of line time to flush
        output = self.buffer[:]
        self.buffer = []
        return output
      s = SAM(line)
      if s.value('qname') != cname: #new entry time to flush
        output = self.buffer[:]
        self.buffer = []
        self.buffer.append(s)
        return output
      self.buffer.append(s)
  def close(self):
    self.fh.close()
    return
  def get_header_string(self):
    ostring = ''
    for line in self.header:
      ostring += line.rstrip()+"\n"
    return ostring

def is_junction_line(line,minlen=68,minoverhang=0):
  prog = re.compile('([0-9]+)([NMX=])')
  f = line.rstrip().split("\t")
  v = prog.findall(f[5])
  #get the indecies of introns
  ns = [i for i in range(0,len(v)) if v[i][1]=='N' and int(v[i][0]) >= minlen]
  if len(ns) == 0: return False
  if minoverhang==0: return True
  good_enough = False
  for intron_index in ns:
    left = sum([int(x[0]) for x in v[0:intron_index] if x[1] != 'N'])
    right = sum([int(x[0]) for x in v[intron_index+1:] if x[1] != 'N'])
    worst = min(left,right)
    if worst >= minoverhang: good_enough = True
  if good_enough: return True
  #sys.exit()
  #v = [y for y in [int(x) for x in prog.findall(f[5])] if y >= minlen]
  #if len(v) == 0: return False
  return False

class SamStream:
  #  minimum_intron_size greater than zero will only show sam entries with introns (junctions)
  #  minimum_overhang greater than zero will require some minimal edge support to consider an intron (junction)
  def __init__(self,fh=None,minimum_intron_size=0,minimum_overhang=0):
    self.previous_line = None
    self.in_header = True
    self.minimum_intron_size = minimum_intron_size
    self.minimum_overhang = minimum_overhang
    if minimum_intron_size <= 0:
      self.junction_only = False
    else: 
      self.junction_only = True
      self.minimum_intron_size = minimum_intron_size
    self.header = []
    if fh:
      self.fh = fh
      self.assign_handle(fh)

  def set_junction_only(self,mybool=True):
    self.junction_only = mybool

  def assign_handle(self,fh):
    if self.in_header:
      while True:
        self.previous_line = fh.readline()
        if is_header(self.previous_line):
          self.header.append(self.previous_line)
        else:
          self.in_header = False
          self.previous_line = self.previous_line
          break
      # make sure our first line is
      if self.junction_only:
        while True:
          if not self.previous_line: break
          if is_junction_line(self.previous_line,self.minimum_intron_size,self.minimum_overhang): break
          self.previous_line = self.fh.readline()

  def __iter__(self):
    return self

  def next(self):
    r = self.read_entry()
    if not r:
      raise StopIteration
    else:
      return r

  def read_entry(self):
    if not self.previous_line: return False
    out = self.previous_line
    self.previous_line = self.fh.readline()
    if self.junction_only:
      while True:
        if not self.previous_line: break
        if is_junction_line(self.previous_line,self.minimum_intron_size,self.minimum_overhang): break
        self.previous_line = self.fh.readline()
    if out: 
      s = SAM(out)
      s.get_range()
      return s
    return None